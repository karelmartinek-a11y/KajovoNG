from __future__ import annotations

from threading import Event


class RunCancelled(RuntimeError):
    """Cooperative cancellation potvrzený business vrstvou."""


class CancellationToken:
    """Thread-safe cancellation bez vazby na Qt nebo worker implementaci."""

    def __init__(self) -> None:
        self._event = Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.is_cancelled():
            raise RunCancelled("Běh byl zrušen.")
