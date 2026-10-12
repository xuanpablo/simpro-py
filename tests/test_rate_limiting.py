"""Client-level rate limiting: built-in pacing, injection, opt-out."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from simpro import (
    DEFAULT_RATE_LIMIT,
    RateLimiter,
    SimproClient,
    SimproError,
    SimproRateLimitError,
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


# --- 429 retry behaviour -------------------------------------------------


def counting_429_handler(statuses: list[int], headers: dict[str, str] | None = None):
    """Answer with the given statuses in order (repeating the last), counting calls."""
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(calls["n"], len(statuses) - 1)
        calls["n"] += 1
        return httpx.Response(statuses[index], json=[], headers=headers)

    return handler, calls


def test_429_retried_honouring_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    seen = {"n": 0}

    def sequencing(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json=[])

    client = make_client(rate_limit=None, transport=httpx.MockTransport(sequencing))
    assert client.companies.list() == []
    assert seen["n"] == 2
    assert sleeps == [1.0]


def test_429_backoff_without_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, calls = counting_429_handler([429, 429, 200])
    client = make_client(rate_limit=None, transport=httpx.MockTransport(handler))
    assert client.companies.list() == []
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_429_exhausted_retries_raise_rate_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, calls = counting_429_handler([429])
    client = make_client(rate_limit=None, transport=httpx.MockTransport(handler))
    with pytest.raises(SimproRateLimitError) as excinfo:
        client.companies.list()
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after is None
    assert calls["n"] == 4  # 1 attempt + 3 retries
    assert sleeps == [1.0, 2.0, 4.0]


def test_429_retry_after_survives_to_final_error(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, _calls = counting_429_handler([429], headers={"Retry-After": "7"})
    client = make_client(rate_limit=None, max_retries=1, transport=httpx.MockTransport(handler))
    with pytest.raises(SimproRateLimitError) as excinfo:
        client.companies.list()
    assert excinfo.value.retry_after == 7.0
    assert sleeps == [7.0]


def test_429_garbage_retry_after_falls_back_to_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, _calls = counting_429_handler([429, 200], headers={"Retry-After": "tomorrow"})
    client = make_client(rate_limit=None, transport=httpx.MockTransport(handler))
    assert client.companies.list() == []
    assert sleeps == [1.0]


def test_429_fractional_retry_after_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, _calls = counting_429_handler([429, 200], headers={"Retry-After": "0.5"})
    client = make_client(rate_limit=None, transport=httpx.MockTransport(handler))
    assert client.companies.list() == []
    assert sleeps == [0.5]


def test_max_retries_zero_disables_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, calls = counting_429_handler([429])
    client = make_client(rate_limit=None, max_retries=0, transport=httpx.MockTransport(handler))
    with pytest.raises(SimproRateLimitError):
        client.companies.list()
    assert calls["n"] == 1
    assert sleeps == []


def test_negative_max_retries_rejected() -> None:
    with pytest.raises(ValueError, match="max_retries"):
        make_client(max_retries=-1)


def test_backoff_capped_at_30_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, _calls = counting_429_handler([429])
    client = make_client(rate_limit=None, max_retries=6, transport=httpx.MockTransport(handler))
    with pytest.raises(SimproRateLimitError):
        client.companies.list()
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0]


def test_other_errors_are_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("simpro.client._sleep", sleeps.append)
    handler, calls = counting_429_handler([500])
    client = make_client(rate_limit=None, transport=httpx.MockTransport(handler))
    with pytest.raises(SimproError) as excinfo:
        client.companies.list()
    assert type(excinfo.value) is SimproError
    assert calls["n"] == 1
    assert sleeps == []


def test_each_retry_attempt_is_paced(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("simpro.client._sleep", lambda seconds: None)
    limiter = CountingLimiter()
    handler, _calls = counting_429_handler([429, 200])
    client = make_client(rate_limiter=limiter, transport=httpx.MockTransport(handler))
    assert client.companies.list() == []
    assert limiter.acquires == 2  # one acquire per attempt, retries included
