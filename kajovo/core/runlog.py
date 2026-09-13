from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .run_bundle import RunBundle, TERMINAL_STATUSES
from .utils import ensure_dir, safe_join_under_root, sha256_file, validate_relative_path

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


def json_artifact_path(
    run_dir: str | Path,
    kind: str,
    run_id: str,
    project_name: str,
    name: str,
) -> Path:
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


def verified_output_evidence(
    run_dir: str | Path, out_dir: str | None = None
) -> list[Dict[str, Any]]:
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
    """Legacy-kompatibilní logger nad kanonickým bezeztrátovým Run Bundle."""

    def __init__(
        self,
        base_log_dir: str,
        run_id: str,
        project_name: str = "",
        *,
        resume: bool = False,
    ):
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
            resumable_response = (
                state.get("response_transport") == "background"
                and state.get("status") != "submission_unknown"
            )
            if (
                (not state.get("generate_batch") and not resumable_response)
                or state.get("batch_id")
                or state.get("submission_unknown")
            ):
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
        for key, path in asdict(self.paths).items():
            if key != "run_id":
                ensure_dir(path)

        self.events_path = os.path.join(self.paths.run_dir, "events.jsonl")
        self.state_path = os.path.join(self.paths.run_dir, "run_state.json")
        self.bundle = RunBundle(
            self.paths.run_dir,
            run_id,
            project=self.project_name,
            create=True,
        )
        if not resume:
            self._write_state(
                {
                    "status": "created",
                    "run_id": run_id,
                    "project": self.project_name,
                    "created_at": time.time(),
                }
            )
        self.event("run.resumed" if resume else "run.created", {"project": self.project_name})

    def _atomic_write_json(self, path: str, payload: Any) -> None:
        ensure_dir(os.path.dirname(path) or ".")
        descriptor, temporary = tempfile.mkstemp(
            prefix=".tmp_", suffix=".json", dir=os.path.dirname(path) or "."
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                try:
                    os.remove(temporary)
                except OSError:
                    pass

    def _redact(self, data: Any) -> Any:
        """Kompatibilní název metody; evidence se záměrně obsahově nemění."""
        return data

    def _write_state(self, state: Dict[str, Any]) -> None:
        self._atomic_write_json(self.state_path, state)

    def _bundle_patch_from_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        ui = state.get("ui_state") if isinstance(state.get("ui_state"), dict) else {}
        mode = str(ui.get("mode") or state.get("mode") or "")
        models = []
        for key in ("model", "model_a1", "model_a2", "model_a3"):
            value = ui.get(key)
            if isinstance(value, str) and value and value not in models:
                models.append(value)
        batches = []
        if isinstance(state.get("batch_id"), str) and state.get("batch_id"):
            batches.append(state["batch_id"])
        if isinstance(state.get("generate_batches"), dict):
            batches.extend(
                value for value in state["generate_batches"] if isinstance(value, str) and value
            )
        status = str(state.get("status") or "created")
        patch: Dict[str, Any] = {
            "project": str(state.get("project") or self.project_name),
            "mode": mode,
            "status": status,
            "result_class": status if status in TERMINAL_STATUSES or status in {"partial", "files_complete_unverified"} else "",
            "related_batch_ids": list(dict.fromkeys(batches)),
            "last_response_id": str(state.get("last_response_id") or ""),
            "model_summary": models,
            "input_summary": str(ui.get("prompt") or "")[:1000],
            "output_summary": str(state.get("error") or "")[:1000],
        }
        created = state.get("created_at")
        if isinstance(created, (int, float)):
            from datetime import datetime, timezone

            patch["created_at"] = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
        if status not in {"created", "preparing"} and not self.bundle.run_record().get("started_at"):
            from datetime import datetime, timezone

            patch["started_at"] = datetime.now(timezone.utc).isoformat()
        completed = state.get("completed_at")
        if isinstance(completed, (int, float)):
            from datetime import datetime, timezone

            patch["finished_at"] = datetime.fromtimestamp(completed, tz=timezone.utc).isoformat()
        elif status in TERMINAL_STATUSES:
            from datetime import datetime, timezone

            patch["finished_at"] = datetime.now(timezone.utc).isoformat()
        configuration = ui or state.get("configuration_snapshot")
        if configuration:
            raw = json.dumps(
                configuration,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
            patch["configuration_snapshot_hash"] = hashlib.sha256(raw).hexdigest()
        return patch

    def _checkpoint_if_needed(self, patch: Dict[str, Any], state: Dict[str, Any]) -> None:
        checkpoint_type = ""
        safe = False
        reason = ""
        if "preparation_snapshot" in patch and isinstance(state.get("preparation_snapshot"), dict):
            snapshot = state["preparation_snapshot"]
            checkpoint_type = str(snapshot.get("canonical_stage") or "prepared_structure")
            safe = True
            reason = "Kanonická příprava byla uložena a lze ji před pokračováním znovu validovat."
        elif "generate_batch" in patch and isinstance(state.get("generate_batch"), dict):
            checkpoint_type = "batch_manifest_prepared"
            safe = True
            reason = "Pracovní Batch manifest je lokálně připraven; vzdálený submit není součástí checkpointu."
        elif patch.get("status") == "files_complete_unverified":
            checkpoint_type = "files_downloaded_validated"
            safe = True
            reason = "Výsledky byly staženy a strukturálně importovány; následná doménová kontrola zůstává oddělena."
        elif patch.get("status") == "completed":
            checkpoint_type = "run_completed"
            safe = True
            reason = "Běh skončil úspěšně a jeho evidence je uzavřená."
        if not checkpoint_type:
            return
        state_hash = hashlib.sha256(
            json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        for existing in self.bundle.checkpoints():
            if existing.get("checkpoint_type") == checkpoint_type and existing.get("state_hash") == state_hash:
                return
        required_responses = []
        if state.get("last_response_id"):
            required_responses.append(str(state["last_response_id"]))
        self.bundle.checkpoint(
            checkpoint_type,
            state_snapshot=state,
            safe_to_continue=safe,
            reason=reason,
            required_response_ids=required_responses,
            invalidation_rules=[
                "Změna modelu, vstupů nebo kanonického přípravného kontraktu invaliduje zděděné navazující kroky.",
                "Chybějící nebo hashově změněný artefakt blokuje automatické pokračování.",
            ],
        )

    def update_state(self, patch: Dict[str, Any]) -> None:
        from .recoverable_artifacts import STATE_ARTIFACTS, save_artifact

        for key in STATE_ARTIFACTS & patch.keys():
            save_artifact(self.paths.run_dir, "state/" + key, patch[key])
        state = {}
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as stream:
                    state = json.load(stream)
        except Exception:
            state = {"status": "corrupt_state"}
        state.update(patch)
        self._write_state(state)
        self.bundle.update_run(self._bundle_patch_from_state(state))
        self.event("state.updated", {"patch": patch, "status": state.get("status")})
        self._checkpoint_if_needed(patch, state)
        if str(state.get("status") or "") in TERMINAL_STATUSES:
            self.bundle.seal()

    def clear_state_keys(self, *keys: str) -> None:
        state = {}
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as stream:
                    state = json.load(stream)
        except Exception:
            state = {"status": "corrupt_state"}
        removed = {}
        for key in keys:
            if key in state:
                removed[key] = state.pop(key)
        self._write_state(state)
        self.bundle.update_run(self._bundle_patch_from_state(state))
        if removed:
            self.event("state.keys_cleared", {"keys": list(removed), "previous_values": removed})

    def event(self, typ: str, data: Dict[str, Any]) -> None:
        severity = "error" if typ.startswith("error.") or typ.endswith("_error") else "warning" if "warning" in typ else "info"
        stage = str(data.get("stage") or data.get("operation") or "") if isinstance(data, dict) else ""
        message = ""
        if isinstance(data, dict):
            message = str(data.get("msg") or data.get("message") or "")
        self.bundle.append_event(
            typ,
            dict(data or {}),
            severity=severity,
            source_module="runlog",
            operation=stage,
            human_message=message,
            technical_message=message,
            related_response_id=str(data.get("response_id") or "") if isinstance(data, dict) else "",
            related_request_id=str(data.get("request_id") or "") if isinstance(data, dict) else "",
        )

    def _json_path(self, kind: str, name: str) -> str:
        return str(
            json_artifact_path(
                self.paths.run_dir,
                kind,
                self.run_id,
                self.project_name,
                name,
            )
        )

    def find_json(self, kind: str, name: str) -> Optional[str]:
        from .recoverable_artifacts import artifact_path

        exact = artifact_path(self.paths.run_dir, kind + "/" + name)
        if exact:
            return exact
        path = self._json_path(kind, name)
        return path if os.path.isfile(path) else None

    def save_json(self, kind: str, name: str, obj: Any) -> str:
        from .recoverable_artifacts import save_artifact

        save_artifact(self.paths.run_dir, kind + "/" + name, obj)
        path = self._json_path(kind, name)
        self._atomic_write_json(path, obj)
        relative = Path(path).relative_to(Path(self.paths.run_dir)).as_posix()
        if kind == "requests":
            self.bundle.record_request(obj, name=name, source_path=relative)
        elif kind == "responses":
            self.bundle.record_response(obj, name=name, source_path=relative)
        else:
            role = {
                "manifests": "manifest",
                "misc": "misc",
                "files": "intermediate_file",
            }.get(kind, kind)
            self.bundle.register_artifact(
                role=role,
                kind="json",
                path_in_bundle=relative,
                original_path=path,
                display_name=Path(path).name,
                source="RunLogger.save_json",
                reusable=kind in {"manifests", "files"},
                reconstruction_role=name,
            )
        self.event(
            f"file.saved.{kind}",
            {"path": path, "bytes": os.path.getsize(path), "name": name},
        )
        return path

    def record_fs_change(
        self,
        action: str,
        src: str,
        dst: Optional[str] = None,
        before: Optional[str] = None,
        after: Optional[str] = None,
        before_size: Optional[int] = None,
        after_size: Optional[int] = None,
    ) -> None:
        self.event(
            "fs.change",
            {
                "action": action,
                "src": src,
                "dst": dst,
                "before": before,
                "after": after,
                "before_size": before_size,
                "after_size": after_size,
            },
        )
        if action == "write" and dst and os.path.isfile(dst):
            try:
                mode = str(self.bundle.run_record().get("mode") or "")
                self.bundle.archive_artifact(
                    dst,
                    role="modified_file" if mode == "MODIFY" else "generated_file",
                    kind="output_file",
                    source="filesystem_write",
                    reusable=True,
                    reconstruction_role=str(src or "output"),
                    metadata={"before_sha256": before, "after_sha256": after},
                )
            except Exception as exc:
                self.event(
                    "artifact.archive_error",
                    {"path": dst, "error": str(exc)},
                )

    def exception(self, where: str, ex: Exception) -> None:
        self.bundle.append_event(
            "error.exception",
            {
                "where": where,
                "type": type(ex).__name__,
                "msg": str(ex),
                "trace": traceback.format_exc(),
            },
            severity="error",
            source_module="runlog",
            operation=where,
            human_message=str(ex),
            technical_message=traceback.format_exc(),
        )

    def record_lineage(self, *args, **kwargs):
        return self.bundle.record_lineage(*args, **kwargs)

    def record_validation(self, **kwargs):
        return self.bundle.record_validation(**kwargs)

    def checkpoint(self, *args, **kwargs):
        return self.bundle.checkpoint(*args, **kwargs)


def find_last_incomplete_run(log_dir: str) -> Optional[str]:
    if not os.path.isdir(log_dir):
        return None
    runs = []
    for name in os.listdir(log_dir):
        if os.path.isdir(os.path.join(log_dir, name)) and name.startswith("RUN_"):
            runs.append(name)
    runs.sort(reverse=True)
    for run_id in runs[:30]:
        state_path = os.path.join(log_dir, run_id, "run_state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as stream:
                state = json.load(stream)
            if state.get("status") not in ("completed", "closed", "failed"):
                return run_id
        except Exception:
            continue
    return None
