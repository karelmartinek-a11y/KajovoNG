"""Qt-nezávislé doménové kontrakty a orchestrace běhů KájovoNG.

Moduly v tomto balíčku nesmějí importovat PySide6 ani UI vrstvy. Přesun z
legacy `core.pipeline` probíhá inkrementálně a po každém kroku musí zůstat
zachováno původní runtime chování.
"""

from .cancellation import CancellationToken, RunCancelled
from .config import UiRunConfig
from .contracts import (
    InvalidRunTransition,
    RunEvent,
    RunFailure,
    RunMode,
    RunPhase,
    RunResult,
    RunStatus,
    TERMINAL_STATUSES,
    phase_for_status,
    validate_transition,
)

__all__ = [
    "CancellationToken",
    "InvalidRunTransition",
    "RunCancelled",
    "RunEvent",
    "RunFailure",
    "RunMode",
    "RunPhase",
    "RunResult",
    "RunStatus",
    "TERMINAL_STATUSES",
    "UiRunConfig",
    "phase_for_status",
    "validate_transition",
]
