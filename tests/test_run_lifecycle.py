from __future__ import annotations

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
    for current, target in zip(states, states[1:], strict=True):
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


def test_unknown_submission_is_terminal_and_only_reachable_from_remote_work():
    validate_transition(RunStatus.REMOTE_WORK, RunStatus.UNKNOWN_REMOTE_SUBMISSION)
    with pytest.raises(InvalidRunTransition):
        validate_transition(RunStatus.UNKNOWN_REMOTE_SUBMISSION, RunStatus.REMOTE_WORK)
    with pytest.raises(InvalidRunTransition):
        validate_transition(RunStatus.PREPARING, RunStatus.UNKNOWN_REMOTE_SUBMISSION)


def test_status_maps_to_framework_independent_phase():
    assert phase_for_status(RunStatus.DELIVERING) is RunPhase.DELIVERING
    assert phase_for_status(RunStatus.CANCELLED) is RunPhase.TERMINAL
