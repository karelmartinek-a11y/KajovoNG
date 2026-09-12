from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import ensure_dir, safe_join_under_root, sha256_file, validate_relative_path

_REDACT_KEYS = {
    "authorization",
    "api_key",
    "openai_api_key",
    "password",
    "ssh_password",
    "smtp_password",
    "token",
    "bearer",
}

_KIND_DIRS = {
    "requests": "requests",
    "responses": "responses",
    "manifests": "manifests",
    "misc": "misc",
}


@dataclass
class RunPaths:
    run_id: str
    run_dir: str
    files_dir: str
    requests_dir: str
    responses_dir: str
    manifests_dir: str
    misc_dir: str


def _safe_component(value: str, limit: int) -> str:
    return "".join(c for c in str(value or "") if c.isalnum() or c in "._-")[:limit]


def json_artifact_filename(run_id: str, project_name: str, name: str) -> str:
    """Kanonický název JSON artefaktu používaný zápisem i recovery."""
    safe = _safe_component(name, 140)
    safe += "_" + hashlib.sha256(str(name).encode("utf-8")).hexdigest()[:12]
    prefix = _safe_component(project_name.strip() or "NO_PROJECT", 60)
    base = f"{prefix}_{run_id}_{safe}" if prefix else f"{run_id}_{safe}"
    return base + ".json"


def json_artifact_path(run_dir: str | Path, kind: str, run_id: str, project_name: str, name: str) -> Path:
    folder = _KIND_DIRS.get(kind, "misc")
    return Path(run_dir) / folder / json_artifact_filename(run_id, project_name, name)


def _read_json_dict(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _saved_entries(record: Dict[str, Any]) -> list[Dict[str, Any]]:
    saved = record.get("saved")
    if isinstance(saved, dict):
        values = []
        for key, value in saved.items():
            row = dict(value) if isinstance(value, dict) else {}
            row.setdefault("path", key)
            values.append(row)
        saved = values
    elif saved is None and isinstance(record.get("out_dir"), str):
        # Konzervativní kompatibilita se starými mapami path -> metadata.
        legacy = []
        for key, value in record.items():
            if key == "out_dir" or not isinstance(key, str):
                continue
            row = dict(value) if isinstance(value, dict) else {}
            row.setdefault("path", key)
            legacy.append(row)
        saved = legacy
    if not isinstance(saved, list):
        return []

    valid = []
    for item in saved:
        if not isinstance(item, dict):
            continue
        rel = item.get("path") or item.get("dst_rel")
        if not isinstance(rel, str) or not rel:
            continue
        try:
            validate_relative_path(rel)
        except ValueError:
            continue
        row = dict(item)
        row["path"] = rel
        digest = row.get("sha256")
        if digest is not None and (not isinstance(digest, str) or len(digest) != 64):
            continue
        valid.append(row)
    return valid


def load_output_evidence(run_dir: str | Path) -> list[Dict[str, Any]]:
    """Vrátí semanticky ověřené důkazy zápisu bez filename-suffix heuristiky."""
    directory = Path(run_dir)
    state = _read_json_dict(directory / "run_state.json")
    run_id = str(state.get("run_id") or directory.name)
    project = str(state.get("project") or "NO_PROJECT")
    manifests = directory / "manifests"
    candidates = []
    for name in ("out_saved_map", "out_write_journal"):
        path = json_artifact_path(directory, "manifests", run_id, project, name)
        if path.is_file():
            candidates.append(path)

    # Legacy fallback: typ artefaktu je potvrzen obsahem, ne názvem souboru.
    exact = {path.resolve() for path in candidates}
    if manifests.is_dir():
        for path in manifests.glob("*.json"):
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved in exact:
                continue
            record = _read_json_dict(path)
            # Starší saved-map artefakty nemusely obsahovat out_dir. Jejich typ je
            # proto určen explicitním polem saved; jednotlivé cesty se níže vždy
            # validují přes validate_relative_path a nebezpečné položky se zahodí.
            if isinstance(record.get("saved"), (list, dict)) and _saved_entries(record):
                candidates.append(path)
            elif isinstance(record.get("out_dir"), str) and _saved_entries(record):
                candidates.append(path)

    merged: Dict[str, Dict[str, Any]] = {}
    for path in candidates:
        record = _read_json_dict(path)
        for entry in _saved_entries(record):
            merged[entry["path"].casefold()] = entry
    return list(merged.values())


def verified_output_evidence(run_dir: str | Path, out_dir: str | None = None) -> list[Dict[str, Any]]:
    """Vrátí pouze důkazy, jejichž cílový soubor stále existuje a případný hash sedí."""
    directory = Path(run_dir)
    state = _read_json_dict(directory / "run_state.json")
    root = str(out_dir or state.get("out_dir") or "").strip()
    if not root:
        return []
    verified = []
    for entry in load_output_evidence(directory):
        try:
            target = safe_join_under_root(root, entry["path"])
        except ValueError:
            continue
        if not os.path.isfile(target):
            continue
        expected = entry.get("sha256")
        if expected and sha256_file(target) != expected:
            continue
        verified.append(entry)
    return verified


class RunLogger:
    def __init__(self, base_log_dir: str, run_id: str, project_name: str = "", *, resume=False):
        root_dir = os.path.abspath(os.curdir)
        if not base_log_dir:
            base_log_dir = os.path.join(root_dir, "LOG")
        if not os.path.isabs(base_log_dir):
            base_log_dir = os.path.join(root_dir, base_log_dir)
        self.base_log_dir = base_log_dir
        if "/" in validate_relative_path(run_id):
            raise ValueError("Identifikátor běhu nesmí obsahovat adresář.")
        self.run_id = run_id
        self.project_name = project_name.strip() or "NO_PROJECT"
        ensure_dir(self.base_log_dir)

        run_dir = os.path.join(self.base_log_dir, run_id)
        if resume:
            with open(os.path.join(run_dir, "run_state.json"), encoding="utf-8") as source:
                state = json.load(source)
            if not state.get("generate_batch") or state.get("batch_id") or state.get("submission_unknown"):
                raise ValueError("Běh nemá dávku bezpečně připravenou k pokračování.")
        else:
            os.makedirs(run_dir, exist_ok=False)
        self.paths = RunPaths(
            run_id=run_id,
            run_dir=run_dir,
            files_dir=os.path.join(run_dir, "files"),
            requests_dir=os.path.join(run_dir, "requests"),
            responses_dir=os.path.join(run_dir, "responses"),
            manifests_dir=os.path.join(run_dir, "manifests"),
            misc_dir=os.path.join(run_dir, "misc"),
        )
        for key, p in asdict(self.paths).items():
            if key != "run_id":
                ensure_dir(p)

        self.events_path = os.path.join(self.paths.run_dir, "events.jsonl")
        self.state_path = os.path.join(self.paths.run_dir, "run_state.json")
        if not resume:
            self._write_state({"status": "created", "run_id": run_id, "project": self.project_name, "created_at": time.time()})
        self.event("run.resumed" if resume else "run.created", {"project": self.project_name})

    def _atomic_write_json(self, path: str, payload: Any) -> None:
        ensure_dir(os.path.dirname(path) or ".")
        fd, tmp_path = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=os.path.dirname(path) or ".")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    def _redact(self, data: Any) -> Any:
        if isinstance(data, dict):
            out: Dict[str, Any] = {}
            for k, v in data.items():
                if str(k).lower() in _REDACT_KEYS:
                    out[k] = "***REDACTED***"
                else:
                    out[k] = self._redact(v)
            return out
        if isinstance(data, list):
            return [self._redact(x) for x in data]
        if isinstance(data, str) and "bearer " in data.lower():
            return "***REDACTED***"
        return data

    def _write_state(self, state: Dict[str, Any]) -> None:
        self._atomic_write_json(self.state_path, self._redact(state))

    def update_state(self, patch: Dict[str, Any]) -> None:
        state = {}
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
        except Exception:
            state = {"status": "corrupt_state"}
        state.update(self._redact(patch))
        self._write_state(state)

    def clear_state_keys(self, *keys: str) -> None:
        """Atomicky odstraní již neplatná pole stavové evidence."""
        state = {}
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
        except Exception:
            state = {"status": "corrupt_state"}
        for key in keys:
            state.pop(key, None)
        self._write_state(state)

    def record_preflight_batch(self, record):
        """Zachová vazbu ověřovací dávky i při okamžitém dokončení nebo chybě."""
        with open(self.state_path, encoding="utf-8") as source:
            state = json.load(source)
        trials = {item["id"]: item for item in state.get("preflight_batches", [])
                  if isinstance(item, dict) and item.get("id")}
        trials[record["id"]] = {**trials.get(record["id"], {}), **record}
        self.update_state({"preflight_batches": list(trials.values())})

    def event(self, typ: str, data: Dict[str, Any]) -> None:
        rec = {"ts": time.time(), "type": typ, "data": self._redact(data)}
        with open(self.events_path, "a", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def _json_path(self, kind: str, name: str) -> str:
        return str(json_artifact_path(self.paths.run_dir, kind, self.run_id, self.project_name, name))

    def find_json(self, kind: str, name: str) -> Optional[str]:
        path = self._json_path(kind, name)
        return path if os.path.isfile(path) else None

    def save_json(self, kind: str, name: str, obj: Any) -> str:
        path = self._json_path(kind, name)
        self._atomic_write_json(path, self._redact(obj))
        self.event(f"file.saved.{kind}", {"path": path, "bytes": os.path.getsize(path)})
        return path

    def record_fs_change(self, action: str, src: str, dst: Optional[str] = None, before: Optional[str] = None, after: Optional[str] = None, before_size: Optional[int] = None, after_size: Optional[int] = None) -> None:
        self.event("fs.change", {"action": action, "src": src, "dst": dst, "before": before, "after": after, "before_size": before_size, "after_size": after_size})

    def exception(self, where: str, ex: Exception) -> None:
        self.event("error.exception", {"where": where, "type": type(ex).__name__, "msg": str(ex), "trace": traceback.format_exc()})


def find_last_incomplete_run(log_dir: str) -> Optional[str]:
    if not os.path.isdir(log_dir):
        return None
    runs = []
    for name in os.listdir(log_dir):
        if os.path.isdir(os.path.join(log_dir, name)) and name.startswith("RUN_"):
            runs.append(name)
    runs.sort(reverse=True)
    for rid in runs[:30]:
        st = os.path.join(log_dir, rid, "run_state.json")
        try:
            with open(st, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("status") not in ("completed", "closed", "failed"):
                return rid
        except Exception:
            continue
    return None
