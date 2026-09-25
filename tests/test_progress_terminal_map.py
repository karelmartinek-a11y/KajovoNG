"""Koncový stav operace nesmí zpětně potvrdit neověřený krok."""

import pytest

from kajovo.core.progress import ProgressEvent
from kajovo.multiprogress import step_states


@pytest.mark.parametrize("terminal,visual", [
    ("completed", "unconfirmed"), ("partial", "unconfirmed"),
    ("failed", "error"), ("submission_unknown", "blocked"),
    ("batch_pending", "blocked"),
])
def test_terminal_preserves_step_evidence(terminal, visual):
    events = [ProgressEvent("A1", "completed"), ProgressEvent("A2", "waiting"),
              ProgressEvent("RUN", terminal)]
    assert step_states(events) == [("A1", "done"), ("A2", visual)]


def test_return_to_earlier_stage_uses_latest_event_as_current():
    events = [ProgressEvent("A1", "completed"), ProgressEvent("A2", "active"),
              ProgressEvent("A1", "repairing")]
    assert step_states(events) == [("A1", "current"), ("A2", "unconfirmed")]
