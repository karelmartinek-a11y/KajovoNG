"""Doložený postup operace; procenta nejsou náhradou za stav běhu."""

from dataclasses import dataclass, field
from collections import deque
from statistics import median
import time


TERMINAL_RUN_STATES = {
    "needs_clarification",
    "completed",
    "completed_unverified",
    "closed",
    "dry_run",
    "plan_ready",
    "qfile_plan_ready",
    "waiting_manual_resource",
    "partial",
    "files_complete_unverified",
    "unfinished_record",
    "cancelled",
    "stopped",
    "failed",
    "error",
    "submission_unknown",
    "corrupt_state",
    "unknown",
    "expired",
    "response_pending",
    "batch_pending",
}


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    state: str = "active"
    completed: int | None = None
    total: int | None = None
    unit: str = ""
    detail: str = ""
    timestamp: float = field(default_factory=time.monotonic)
    # Volitelná metadata pro společné živé zobrazení. Přidávají se až za
    # původní argumenty, aby starší poziční volání zůstala kompatibilní.
    source: str = "local"
    next_step: str = ""
    phase_index: int | None = None
    phase_total: int | None = None
    operation: str = ""
    project: str = ""
    run_id: str = ""
    model: str = ""
    provider_state: str = ""
    attempt: int | None = None
    attempt_total: int | None = None
    response_id: str = ""
    batch_id: str = ""
    file_id: str = ""
    path: str = ""


class ProgressClock:
    def __init__(self, now=None):
        self.started = time.monotonic() if now is None else now
        self.last_activity = self.started
        self.stage = "Příprava"
        self.state = "active"
        self.completed = 0
        self.total = None
        self.unit = ""
        self.samples = deque(maxlen=5)
        self.unit_started = self.started
        self.finished = None
        self.last_event = None
        self.stage_started = self.started

    def update(self, event):
        self.last_event = event
        if event.stage != self.stage:
            self.stage = event.stage
            self.samples.clear()
            self.completed, self.total = 0, None
            self.unit_started = event.timestamp
            self.stage_started = event.timestamp
        self.last_activity = event.timestamp
        self.state = event.state
        if event.total is not None:
            self.total = event.total
            self.unit = event.unit
        if event.completed is not None:
            if event.completed == self.completed + 1:
                self.samples.append(max(0, event.timestamp - self.unit_started))
            if event.completed != self.completed:
                self.unit_started = event.timestamp
            self.completed = event.completed
        if event.state in TERMINAL_RUN_STATES and event.stage == "RUN":
            self.finished = event.timestamp

    def times(self, now=None):
        now = time.monotonic() if now is None else now
        effective_now = self.finished if self.finished is not None else now
        elapsed = max(0, effective_now - self.started)
        eta = None
        if self.finished is None and self.total and len(self.samples) >= 3:
            eta = max(
                0, (self.total - self.completed) * median(self.samples) - (now - self.unit_started)
            )
        return elapsed, max(0, now - self.last_activity), eta

    def stage_elapsed(self, now=None):
        now = time.monotonic() if now is None else now
        effective_now = self.finished if self.finished is not None else now
        return max(0, effective_now - self.stage_started)
