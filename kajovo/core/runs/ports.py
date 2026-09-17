from __future__ import annotations

from collections.abc import Callable
from threading import RLock
from typing import Any


class EventPort:
    """Minimální synchronní event port se signal-like rozhraním pro core orchestrace."""

    def __init__(self) -> None:
        self._listeners: list[Callable[..., Any]] = []
        self._lock = RLock()

    def connect(self, listener: Callable[..., Any]) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def disconnect(self, listener: Callable[..., Any]) -> None:
        with self._lock:
            self._listeners.remove(listener)

    def emit(self, *args: Any, **kwargs: Any) -> None:
        with self._lock:
            listeners = tuple(self._listeners)
        for listener in listeners:
            listener(*args, **kwargs)
