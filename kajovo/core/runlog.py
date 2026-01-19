from __future__ import annotations

import os, json, time, traceback
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
from .utils import ensure_dir

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
    def __init__(self, base_log_dir: str, run_id: str, project_name: str = ""):
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        if not base_log_dir:
            base_log_dir = os.path.join(root_dir, "LOG")
        if not os.path.isabs(base_log_dir):
            base_log_dir = os.path.join(root_dir, base_log_dir)
        self.base_log_dir = base_log_dir
        self.run_id = run_id
        self.project_name = (project_name.strip() or "NO_PROJECT")
        ensure_dir(self.base_log_dir)

        run_dir = os.path.join(self.base_log_dir, run_id)
        self.paths = RunPaths(
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
        self._write_state({"status":"created","run_id":run_id,"project":self.project_name,"created_at":time.time()})
        self.event("run.created", {"project": self.project_name})

    def _write_state(self, state: Dict[str, Any]) -> None:
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)

    def update_state(self, patch: Dict[str, Any]) -> None:
        state = {}
        try:
            if os.path.exists(self.state_path):
                with open(self.state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
        except Exception:
            state = {"status":"corrupt_state"}
        state.update(patch)
        self._write_state(state)

    def event(self, typ: str, data: Dict[str, Any]) -> None:
        rec = {"ts": time.time(), "type": typ, "data": data}
        with open(self.events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def save_json(self, kind: str, name: str, obj: Any) -> str:
        folder = {
            "requests": self.paths.requests_dir,
            "responses": self.paths.responses_dir,
            "manifests": self.paths.manifests_dir,
            "misc": self.paths.misc_dir,
        }.get(kind, self.paths.misc_dir)
        safe = "".join(c for c in name if c.isalnum() or c in "._-")[:140]
        prefix = "".join(c for c in self.project_name if c.isalnum() or c in "._-")[:60]
        safe2 = f"{prefix}_{self.run_id}_{safe}" if prefix else f"{self.run_id}_{safe}"
        path = os.path.join(folder, f"{safe2}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, default=str)
        self.event(f"file.saved.{kind}", {"path": path, "bytes": os.path.getsize(path)})
        return path

    def record_fs_change(self, action: str, src: str, dst: Optional[str]=None, before: Optional[str]=None, after: Optional[str]=None, before_size: Optional[int]=None, after_size: Optional[int]=None) -> None:
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
            if state.get("status") not in ("completed","closed","failed"):
                return rid
        except Exception:
            continue
    return None
