from __future__ import annotations

from itertools import pairwise

import pytest

from kajovo.core.runs.contracts import (
    InvalidRunTransition,
    RunPhase,
    RunStatus,
    phase_for_status,
    validate_transition,
)


def test_happy_path_lifecycle_is_valid_with_delivery():
    states = [
        RunStatus.CREATED,
        RunStatus.PREPARING,
        RunStatus.REMOTE_WORK,
        RunStatus.PROCESSING_RESPONSE,
        RunStatus.DELIVERING,
        RunStatus.FINALIZING,
        RunStatus.COMPLETED,
    ]
    for current, target in pairwise(states):
        validate_transition(current, target)


def test_happy_path_lifecycle_can_skip_delivery_for_qa_like_workflow():
    validate_transition(RunStatus.PROCESSING_RESPONSE, RunStatus.FINALIZING)
    validate_transition(RunStatus.FINALIZING, RunStatus.COMPLETED)


def test_completed_cannot_return_to_work_and_success_cannot_skip_finalizing():
    with pytest.raises(InvalidRunTransition):
        validate_transition(RunStatus.COMPLETED, RunStatus.PREPARING)
    with pytest.raises(InvalidRunTransition):
        validate_transition(RunStatus.REMOTE_WORK, RunStatus.COMPLETED)
    with pytest.raises(InvalidRunTransition):
        validate_transition(RunStatus.DELIVERING, RunStatus.COMPLETED)


def test_unknown_submission_is_terminal_and_requires_remote_work_started():
    validate_transition(RunStatus.REMOTE_WORK, RunStatus.UNKNOWN_REMOTE_SUBMISSION)
    validate_transition(RunStatus.PROCESSING_RESPONSE, RunStatus.UNKNOWN_REMOTE_SUBMISSION)
    with pytest.raises(InvalidRunTransition):
        validate_transition(RunStatus.UNKNOWN_REMOTE_SUBMISSION, RunStatus.REMOTE_WORK)
    with pytest.raises(InvalidRunTransition):
        validate_transition(RunStatus.PREPARING, RunStatus.UNKNOWN_REMOTE_SUBMISSION)


def test_status_maps_to_framework_independent_phase():
    assert phase_for_status(RunStatus.DELIVERING) is RunPhase.DELIVERING
    assert phase_for_status(RunStatus.CANCELLED) is RunPhase.TERMINAL


def test_executor_preserves_waiting_manual_resource_without_marking_completed(tmp_path, monkeypatch):
    import json
    from pathlib import Path
    from types import SimpleNamespace

    from change_v2_fixtures import run, scenario

    from kajovo.core.progress import TERMINAL_RUN_STATES
    from kajovo.core.runs.executor import WORKFLOWS
    worker, client, _ = scenario(tmp_path, "GENERATE")
    monkeypatch.setitem(WORKFLOWS, "GENERATE", SimpleNamespace(execute=lambda context: {
        "mode": "GENERATE", "status": "waiting_manual_resource", "missing_deliverables": ["data.bin"]}))
    results, errors = run(worker, client)
    assert not errors and results[0]["status"] == "waiting_manual_resource"
    state = json.loads(Path(worker.log.state_path).read_text(encoding="utf-8"))
    assert state["status"] == "waiting_manual_resource" and "completed_at" not in state
    assert "waiting_manual_resource" in TERMINAL_RUN_STATES
    client.create_response.assert_not_called()
