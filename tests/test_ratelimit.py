"""Sliding-window rate limiter unit behaviour (fake clock, no real sleeps)."""

from __future__ import annotations

import pytest

from simpro.ratelimit import DEFAULT_RATE_LIMIT, SlidingWindowRateLimiter


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


def make_limiter(rate: float) -> tuple[SlidingWindowRateLimiter, FakeClock]:
    clock = FakeClock()
    return SlidingWindowRateLimiter(rate, timer=clock.timer, sleeper=clock.sleeper), clock


def test_default_rate_limit_just_under_simpro_ceiling() -> None:
    # SimPro's documented ceiling is 10/s shared across ALL integrations on
    # the build — the default leaves headroom for that other traffic.
    assert DEFAULT_RATE_LIMIT == 9.0


def test_burst_up_to_budget_needs_no_sleep() -> None:
    limiter, clock = make_limiter(9)
    for _ in range(9):
        limiter.acquire()
    assert clock.sleeps == []


def test_request_beyond_budget_waits_for_oldest_to_expire() -> None:
    limiter, clock = make_limiter(9)
    for _ in range(9):
        limiter.acquire()
    limiter.acquire()
    # Window is 1.0s wide and full of timestamps at t=1000: the 10th call
    # must wait exactly one second for them to age out.
    assert clock.sleeps == [1.0]


def test_budget_resets_each_window_for_long_pulls() -> None:
    limiter, clock = make_limiter(3)
    for _ in range(12):
        limiter.acquire()
    # 3 free at t=1000, then one 1s wait per exhausted window: calls 4, 7, 10.
    assert clock.sleeps == [1.0, 1.0, 1.0]


def test_fractional_rate_stretches_the_window() -> None:
    limiter, clock = make_limiter(0.5)  # -> 1 event per 2.0s window
    limiter.acquire()
    limiter.acquire()
    assert clock.sleeps == [2.0]


def test_non_positive_rate_limit_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        SlidingWindowRateLimiter(0)
    with pytest.raises(ValueError, match="positive"):
        SlidingWindowRateLimiter(-5)
