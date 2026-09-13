"""Kaskádové logování používá stejný kanonický Run Bundle jako ostatní běhy."""
from __future__ import annotations

from .runlog import RunLogger


class CascadeLogger(RunLogger):
    """Kompatibilní specializace hlavního loggeru pro KASKÁDA workflow."""

    def __init__(self, base_log_dir: str, run_id: str, project_name: str = ""):
        super().__init__(base_log_dir, run_id, project_name=project_name)
        self.update_state({"kind": "cascade", "mode": "KASKADA"})
        self.bundle.update_run({"kind": "cascade", "mode": "KASKADA"})
        self.event("cascade.created", {"project": self.project_name, "kind": "cascade"})
