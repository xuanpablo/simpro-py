"""Client-level rate limiting: built-in pacing, injection, opt-out."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from simpro import (
    DEFAULT_RATE_LIMIT,
    RateLimiter,
    SimproClient,
    SlidingWindowRateLimiter,
)


class FakeClock:
    """Deterministic timer/sleeper pair: sleeping advances the clock."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def timer(self) -> float:
        return self.now

    def sleeper(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class CountingLimiter:
    """Duck-typed RateLimiter that records acquire() calls."""

    def __init__(self) -> None:
        self.acquires = 0

    def acquire(self) -> None:
        self.acquires += 1


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[])


def make_client(**kwargs: Any) -> SimproClient:
    kwargs.setdefault("transport", httpx.MockTransport(ok_handler))
    return SimproClient("https://t.simprosuite.com", "k", **kwargs)


def test_default_client_paces_at_default_rate_limit() -> None:
    client = make_client()
    assert isinstance(client._limiter, SlidingWindowRateLimiter)
    assert DEFAULT_RATE_LIMIT == 9.0


def test_rate_limit_none_disables_pacing() -> None:
    client = make_client(rate_limit=None)
    assert client._limiter is None
    for _ in range(50):  # rapid burst: no exception, and no limiter to sleep
        client.companies.list()


def test_invalid_rate_limit_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        make_client(rate_limit=0)


def test_requests_are_paced_by_injected_limiter() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(9, timer=clock.timer, sleeper=clock.sleeper)
    client = make_client(rate_limiter=limiter)
    for _ in range(10):
        client.companies.list()
    # 9 free at t=1000; the 10th waits one second for the window to clear.
    assert clock.sleeps == [1.0]


def test_limiter_shared_across_clients_shares_one_budget() -> None:
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(9, timer=clock.timer, sleeper=clock.sleeper)
    a = make_client(rate_limiter=limiter)
    b = make_client(rate_limiter=limiter)
    for _ in range(9):
        a.companies.list()
    b.companies.list()  # 10th acquisition overall: must wait.
    assert clock.sleeps == [1.0]


def test_custom_limiter_protocol_accepted() -> None:
    limiter: RateLimiter = CountingLimiter()
    client = make_client(rate_limiter=limiter)
    client.companies.list()
    client.customers.get(0, 7)
    assert limiter.acquires == 2
