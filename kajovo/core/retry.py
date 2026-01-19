from __future__ import annotations

import time, random
from typing import Callable, TypeVar, Optional
from .config import RetryPolicy
from .openai_client import OpenAIError

T = TypeVar("T")

class CircuitBreaker:
    def __init__(self, failures: int, cooldown_s: float):
        self.failures = failures
        self.cooldown_s = cooldown_s
        self._count = 0
        self._open_until = 0.0

    def allow(self) -> bool:
        return time.time() >= self._open_until

    def on_success(self) -> None:
        self._count = 0
        self._open_until = 0.0

    def on_failure(self) -> None:
        self._count += 1
        if self._count >= self.failures:
            self._open_until = time.time() + self.cooldown_s

def with_retry(fn: Callable[[], T], policy: RetryPolicy, breaker: Optional[CircuitBreaker]=None) -> T:
    last: Optional[Exception] = None
    for attempt in range(1, policy.max_attempts + 1):
        if breaker and not breaker.allow():
            time.sleep(min(policy.circuit_breaker_cooldown_s, 3.0))
            continue
        try:
            out = fn()
            if breaker:
                breaker.on_success()
            return out
        except OpenAIError as e:
            last = e
            msg = str(e)
            transient = any(code in msg for code in (" 429:", " 500:", " 502:", " 503:", " 504:"))
            if not transient:
                raise
        except (TimeoutError, OSError) as e:
            last = e
        if breaker:
            breaker.on_failure()
        delay = min(policy.max_delay_s, policy.base_delay_s * (2 ** (attempt-1)))
        delay += random.random() * policy.jitter_s
        time.sleep(delay)
    if last:
        raise last
    raise RuntimeError("retry_failed")
