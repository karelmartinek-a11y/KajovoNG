from __future__ import annotations

import pytest

from kajovo.core.runs.contracts import (
    RunEvent,
    RunFailure,
    RunMode,
    RunPhase,
    RunResult,
    RunStatus,
)


def test_run_mode_is_closed_to_supported_workflows():
    assert {mode.value for mode in RunMode} == {"GENERATE", "MODIFY", "QA", "QFILE"}


def test_run_event_rejects_invalid_progress_and_is_immutable():
    event = RunEvent(RunPhase.PREPARING, 10, "Příprava", {"step": 1})
    assert event.detail == {"step": 1}
    with pytest.raises(TypeError):
        event.detail["step"] = 2  # type: ignore[index]
    with pytest.raises(ValueError):
        RunEvent(RunPhase.PREPARING, 101, "Příprava")


def test_run_result_distinguishes_completed_and_unknown_submission():
    success = RunResult(RunStatus.COMPLETED, {"response_id": "resp_1"})
    unknown = RunResult(
        RunStatus.UNKNOWN_REMOTE_SUBMISSION,
        failure=RunFailure("SUBMISSION_UNKNOWN", "Výsledek submitu není potvrzen.", True),
    )
    assert success.succeeded is True
    assert unknown.succeeded is False
    assert unknown.status is RunStatus.UNKNOWN_REMOTE_SUBMISSION


def test_non_success_result_requires_failure():
    with pytest.raises(ValueError):
        RunResult(RunStatus.FAILED)
