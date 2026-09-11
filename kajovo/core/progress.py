"""Doložený postup operace; procenta nejsou náhradou za stav běhu."""

from dataclasses import dataclass, field
from collections import deque
from statistics import median
import time


@dataclass(frozen=True)
class ProgressEvent:
    stage: str
    state: str = "active"
    completed: int | None = None
    total: int | None = None
    unit: str = ""
    detail: str = ""
    timestamp: float = field(default_factory=time.monotonic)


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

    def update(self, event):
        if event.stage != self.stage:
            self.stage = event.stage
            self.samples.clear()
            self.completed, self.total = 0, None
            self.unit_started = event.timestamp
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
        if event.state in ("completed", "failed", "cancelled", "batch_pending", "preflight_pending") and event.stage == "RUN":
            self.finished = event.timestamp

    def times(self, now=None):
        now = time.monotonic() if now is None else now
        elapsed = max(0, (self.finished if self.finished is not None else now) - self.started)
        eta = None
        if self.finished is None and self.total and len(self.samples) >= 3:
            eta = max(
                0, (self.total - self.completed) * median(self.samples) - (now - self.unit_started)
            )
        return elapsed, max(0, now - self.last_activity), eta
