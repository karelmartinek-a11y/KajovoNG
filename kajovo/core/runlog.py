from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

from .utils import ensure_dir, validate_relative_path

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


@dataclass
class RunPaths:
    run_id: str
    run_dir: str
    files_dir: str
    requests_dir: str
    responses_dir: str
    manifests_dir: str
    misc_dir: str


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

    def save_json(self, kind: str, name: str, obj: Any) -> str:
        folder = {
            "requests": self.paths.requests_dir,
            "responses": self.paths.responses_dir,
            "manifests": self.paths.manifests_dir,
            "misc": self.paths.misc_dir,
        }.get(kind, self.paths.misc_dir)
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:140]
        safe += "_" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        prefix = "".join(c for c in self.project_name if c.isalnum() or c in "._-")[:60]
        safe2 = f"{prefix}_{self.run_id}_{safe}" if prefix else f"{self.run_id}_{safe}"
        path = os.path.join(folder, f"{safe2}.json")
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
