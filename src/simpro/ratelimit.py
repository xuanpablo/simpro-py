"""Client-side request pacing for SimPro's per-build rate limit.

SimPro enforces 10 API calls per second, per build and per IP (announced
August 2022, strictly enforced since 17 April 2025), and the budget is
shared across every integration hitting the build — not just this client.
``SlidingWindowRateLimiter`` keeps this SDK inside the ceiling by starting
no more than ``rate_limit`` requests in any trailing second.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Protocol

# SimPro's documented ceiling is 10 requests/second per build + IP, shared
# with all other integrations and UI traffic on the build. The default
# stays just under it to leave headroom for that other traffic.
DEFAULT_RATE_LIMIT = 9.0

Timer = Callable[[], float]
Sleeper = Callable[[float], None]


class RateLimiter(Protocol):
    """Anything with an ``acquire()`` that blocks until a request may start.

    Implement this protocol to plug in a custom limiter and pass it as
    ``SimproClient(rate_limiter=...)``.
    """

    def acquire(self) -> None: ...


class SlidingWindowRateLimiter:
    """Start at most ``rate_limit`` acquisitions per second, sliding window.

    Keeps timestamps of recent acquisitions in a trailing window; when the
    window is full, ``acquire`` sleeps until the oldest timestamp expires.
    A burst first spends the whole budget immediately, then requests pace
    at ``rate_limit`` per second.

    A lock serialises ``acquire`` across threads sharing one limiter, so
    multi-threaded pulls through one client (or several clients on the
    same build) count against a single budget.

    Args:
        rate_limit: Allowed acquisitions per second. Must be positive.
            Values below 1 stretch the window (0.5 -> one call per 2s).
        timer: Clock callable (seconds, monotonic); injectable for tests.
        sleeper: Sleep callable; injectable for tests.
    """

    def __init__(
        self,
        rate_limit: float,
        *,
        timer: Timer = time.monotonic,
        sleeper: Sleeper = time.sleep,
    ) -> None:
        if rate_limit <= 0:
            raise ValueError(f"rate_limit must be positive, got {rate_limit!r}")
        self._max_events = max(1, int(rate_limit))
        self._window = self._max_events / rate_limit
        self._timer = timer
        self._sleeper = sleeper
        self._starts: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until one acquisition fits inside the rate budget."""
        with self._lock:
            now = self._prune(self._timer())
            while len(self._starts) >= self._max_events:
                sleep_for = self._window - (now - self._starts[0])
                if sleep_for > 0:
                    self._sleeper(sleep_for)
                now = self._prune(self._timer())
            self._starts.append(now)

    def _prune(self, now: float) -> float:
        while self._starts and now - self._starts[0] >= self._window:
            self._starts.popleft()
        return now
