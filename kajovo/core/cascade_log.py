from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict

from .utils import ensure_dir


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
class CascadeRunPaths:
    run_id: str
    run_dir: str
    files_dir: str
    requests_dir: str
    responses_dir: str
    manifests_dir: str
    misc_dir: str


class CascadeLogger:
    def __init__(self, base_log_dir: str, run_id: str, project_name: str = ""):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if not base_log_dir:
            base_log_dir = os.path.join(root_dir, "LOG")
        if not os.path.isabs(base_log_dir):
            base_log_dir = os.path.join(root_dir, base_log_dir)
        self.base_log_dir = base_log_dir
        self.run_id = run_id
        self.project_name = project_name.strip() or "NO_PROJECT"
        ensure_dir(self.base_log_dir)

        run_dir = os.path.join(self.base_log_dir, run_id)
        self.paths = CascadeRunPaths(
            run_id=run_id,
            run_dir=run_dir,
            files_dir=os.path.join(run_dir, "files"),
            requests_dir=os.path.join(run_dir, "requests"),
            responses_dir=os.path.join(run_dir, "responses"),
            manifests_dir=os.path.join(run_dir, "manifests"),
            misc_dir=os.path.join(run_dir, "misc"),
        )
        for p in asdict(self.paths).values():
            ensure_dir(p)

        self.events_path = os.path.join(self.paths.run_dir, "events.jsonl")
        self.state_path = os.path.join(self.paths.run_dir, "run_state.json")
        self._write_state({
            "status": "created",
            "run_id": run_id,
            "project": self.project_name,
            "created_at": time.time(),
            "kind": "cascade",
        })
        self.event("run.created", {"project": self.project_name, "kind": "cascade"})

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
            "files": self.paths.files_dir,
        }.get(kind, self.paths.misc_dir)
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:140]
        prefix = "".join(c for c in self.project_name if c.isalnum() or c in "._-")[:60]
        safe2 = f"{prefix}_{self.run_id}_{safe}" if prefix else f"{self.run_id}_{safe}"
        path = os.path.join(folder, f"{safe2}.json")
        self._atomic_write_json(path, self._redact(obj))
        self.event(f"file.saved.{kind}", {"path": path, "bytes": os.path.getsize(path)})
        return path
