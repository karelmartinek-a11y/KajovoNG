from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .context import RunContext


class WorkflowExecutor(Protocol):
    """Workflow používá služby běhu a vrací výsledek pro obecnou finalizaci."""

    def execute(self, context: RunContext) -> dict[str, Any]: ...


class RunMode(StrEnum):
    """Kanonické produktové workflow podporované run orchestrací."""

    GENERATE = "GENERATE"
    MODIFY = "MODIFY"
    QA = "QA"
    QFILE = "QFILE"


class RunStatus(StrEnum):
    """Stav běhu nezávislý na konkrétní persistence/UI reprezentaci."""

    CREATED = "created"
    PREPARING = "preparing"
    REMOTE_WORK = "remote_work"
    PROCESSING_RESPONSE = "processing_response"
    DELIVERING = "delivering"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"
    UNKNOWN_REMOTE_SUBMISSION = "unknown_remote_submission"


class RunPhase(StrEnum):
    """Významová fáze pro progress události."""

    PREPARING = "preparing"
    REMOTE_WORK = "remote_work"
    PROCESSING_RESPONSE = "processing_response"
    DELIVERING = "delivering"
    FINALIZING = "finalizing"
    TERMINAL = "terminal"


TERMINAL_STATUSES = frozenset(
    {
        RunStatus.COMPLETED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.INTERRUPTED,
        RunStatus.UNKNOWN_REMOTE_SUBMISSION,
    }
)

_ALLOWED_TRANSITIONS: Mapping[RunStatus, frozenset[RunStatus]] = MappingProxyType(
    {
        RunStatus.CREATED: frozenset(
            {RunStatus.PREPARING, RunStatus.CANCELLED, RunStatus.FAILED}
        ),
        RunStatus.PREPARING: frozenset(
            {
                RunStatus.REMOTE_WORK,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.INTERRUPTED,
            }
        ),
        RunStatus.REMOTE_WORK: frozenset(
            {
                RunStatus.PROCESSING_RESPONSE,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.INTERRUPTED,
                RunStatus.UNKNOWN_REMOTE_SUBMISSION,
            }
        ),
        RunStatus.PROCESSING_RESPONSE: frozenset(
            {
                RunStatus.DELIVERING,
                RunStatus.FINALIZING,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.INTERRUPTED,
            }
        ),
        RunStatus.DELIVERING: frozenset(
            {
                RunStatus.FINALIZING,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
                RunStatus.INTERRUPTED,
            }
        ),
        RunStatus.FINALIZING: frozenset(
            {
                RunStatus.COMPLETED,
                RunStatus.FAILED,
                RunStatus.INTERRUPTED,
            }
        ),
        RunStatus.COMPLETED: frozenset(),
        RunStatus.FAILED: frozenset(),
        RunStatus.CANCELLED: frozenset(),
        RunStatus.INTERRUPTED: frozenset(),
        RunStatus.UNKNOWN_REMOTE_SUBMISSION: frozenset(),
    }
)


@dataclass(frozen=True, slots=True)
class RunEvent:
    phase: RunPhase
    progress: int
    message: str
    detail: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.progress <= 100:
            raise ValueError("Progress běhu musí být v rozsahu 0 až 100.")
        if not self.message.strip():
            raise ValueError("RunEvent musí mít neprázdnou zprávu.")
        if self.detail is not None:
            object.__setattr__(self, "detail", MappingProxyType(dict(self.detail)))


@dataclass(frozen=True, slots=True)
class RunFailure:
    code: str
    message: str
    recoverable: bool = False
    cause_type: str | None = None

    def __post_init__(self) -> None:
        if not self.code.strip() or not self.message.strip():
            raise ValueError("RunFailure vyžaduje code i message.")


@dataclass(frozen=True, slots=True)
class RunResult:
    status: RunStatus
    value: Mapping[str, Any] | None = None
    failure: RunFailure | None = None

    def __post_init__(self) -> None:
        if self.status not in TERMINAL_STATUSES:
            raise ValueError("RunResult smí nést pouze terminální stav.")
        if self.status is RunStatus.COMPLETED and self.failure is not None:
            raise ValueError("Úspěšný RunResult nesmí obsahovat failure.")
        if self.status is not RunStatus.COMPLETED and self.failure is None:
            raise ValueError("Neúspěšný RunResult musí obsahovat RunFailure.")
        if self.value is not None:
            object.__setattr__(self, "value", MappingProxyType(dict(self.value)))

    @property
    def succeeded(self) -> bool:
        return self.status is RunStatus.COMPLETED


class InvalidRunTransition(ValueError):
    """Pokus o stavový přechod, který porušuje lifecycle kontrakt."""


def validate_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise InvalidRunTransition(f"Neplatný přechod běhu: {current.value} -> {target.value}")


def phase_for_status(status: RunStatus) -> RunPhase:
    mapping = {
        RunStatus.CREATED: RunPhase.PREPARING,
        RunStatus.PREPARING: RunPhase.PREPARING,
        RunStatus.REMOTE_WORK: RunPhase.REMOTE_WORK,
        RunStatus.PROCESSING_RESPONSE: RunPhase.PROCESSING_RESPONSE,
        RunStatus.DELIVERING: RunPhase.DELIVERING,
        RunStatus.FINALIZING: RunPhase.FINALIZING,
    }
    return mapping.get(status, RunPhase.TERMINAL)
