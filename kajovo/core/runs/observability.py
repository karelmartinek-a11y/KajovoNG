from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Protocol

LOGGER = logging.getLogger(__name__)


class EventLogger(Protocol):
    def event(self, event: str, payload: Mapping[str, Any]) -> Any: ...

    def update_state(self, patch: Mapping[str, Any]) -> Any: ...


def record_event(logger: EventLogger, event: str, payload: Mapping[str, Any]) -> bool:
    """Best-effort evidence zápis bez tichého spolknutí chyby a bez dumpu payloadu."""
    try:
        logger.event(event, dict(payload))
        return True
    except Exception as exc:
        LOGGER.warning(
            "Forensic event write failed: event=%s error=%s",
            event,
            type(exc).__name__,
        )
        return False


def update_state(logger: EventLogger, patch: Mapping[str, Any]) -> bool:
    """Best-effort state patch; do fallback logu patří pouze typ chyby, ne stavová data."""
    try:
        logger.update_state(dict(patch))
        return True
    except Exception as exc:
        LOGGER.warning("Run state update failed: error=%s", type(exc).__name__)
        return False


def emit_signal(signal: Any, value: Any, *, name: str) -> bool:
    """UI signál nesmí skrýt chybu evidence; selhání signalizace je dohledatelné v stderr logu."""
    try:
        signal.emit(value)
        return True
    except Exception as exc:
        LOGGER.warning("Qt signal emit failed: signal=%s error=%s", name, type(exc).__name__)
        return False
