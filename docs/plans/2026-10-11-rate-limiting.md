# Built-in Rate Limiting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Pace every `SimproClient` request inside SimPro's documented rate limit and recover automatically from HTTP 429 answers.

**Architecture:** A new `simpro/ratelimit.py` module owns pacing: a sliding one-second window (`SlidingWindowRateLimiter`) that starts no more than N requests in any trailing second, with injectable timer/sleeper for deterministic tests and a lock so threads sharing one limiter share one budget. The client wires the limiter in front of every request attempt (including retries) and adds a bounded 429 retry loop that honours `Retry-After` when SimPro sends it, with exponential backoff otherwise. `SimproRateLimitError` gains a parsed `retry_after` attribute.

**Tech Stack:** Python 3.12+ stdlib only (`threading`, `time`, `collections.deque`, `typing.Protocol`) — no new runtime dependencies. Tests use the existing `pytest` + `httpx.MockTransport` pattern.

**Spec:** No separate spec document exists; the requirement is the user's request ("add built in rate limiting that stays within SimPro's defined rules") plus SimPro's own published rules, which are the authoritative spec for "defined rules":

- SimPro Developer Center, "API Call Limits": https://developer.simprogroup.com/apidoc/?page=ed8457e003ba0f6197756eca5a61fde9 — "If we detect that a single Simpro client build is receiving more than 10 API requests per second from an integration then we will respond to all subsequent requests with an HTTP 429 (too many requests) response until the requests fall back under the limit."
- SimPro API forum announcement (17 Apr 2025): https://apiforum.simprogroup.com/viewtopic.php?t=2902 — the 10 calls/sec limit is **strictly enforced since 17 April 2025**, and "Rate limiting is per IP and per build" (i.e. the budget is shared across every integration and company hitting the build, not per API key).
- SimPro's official 429 guidance is "queuing, sleep mechanism, backoff and retry logic" — integrations report SimPro sends a `Retry-After` header (delay-seconds form) on 429s.

## Global Constraints

- Python `>=3.12`; the only runtime dependency is `httpx` — **no new dependencies may be added**.
- The limit the SDK must stay within: **10 requests/second per build and per IP**, shared across all integrations on the build.
- Default pacing rate is `DEFAULT_RATE_LIMIT = 9.0` requests/second (headroom under the shared 10/s ceiling); `rate_limit=None` opts out and restores old no-pacing behaviour.
- Only HTTP 429 is retried automatically; transport errors, 5xx, 4xx are never retried (single attempt, existing exception mapping unchanged).
- `uv run ruff check .` and `uv run ruff format --check .` must pass (line length 100; lint rules E, F, W, I, UP, B, SIM).
- `uv run mypy` must pass (checks `src/simpro` only).
- Tests must be deterministic and fast: **no real `time.sleep` in any test** — use injectable fake clocks and monkeypatched sleep.
- Plain-dict responses, no DTO layer, synchronous client only (existing library conventions).
- Version bump to `0.2.0` (new feature + default-behaviour change: pacing is on by default).

## Review Focus

The five input classes / failure modes most likely to bite a person, each pinned to the task whose tests exercise it:

1. **Garbage or absent `Retry-After` header on a 429** — parsing must degrade to `None` (never raise), and the retry must fall back to its own backoff. Tests: Task 3 `test_rate_limit_error_retry_after_defaults_to_none`; Task 4 `test_429_garbage_retry_after_falls_back_to_backoff` and `test_429_backoff_without_retry_after`.
2. **Invalid configuration values** (`rate_limit=0`, negative rate, `max_retries=-1`) — must fail fast with `ValueError` at construction, not misbehave at request time. Tests: Task 1 `test_non_positive_rate_limit_rejected`; Task 2 `test_invalid_rate_limit_rejected`; Task 4 `test_negative_max_retries_rejected`.
3. **Persistent 429s must terminate** — retries exhaust and raise `SimproRateLimitError` carrying the last `retry_after`; the client must never loop forever or return a 429 response as success. Tests: Task 4 `test_429_exhausted_retries_raise_rate_limit_error`, `test_429_retry_after_survives_to_final_error`.
4. **Long bulk pulls must neither stall nor exceed budget** — the sliding window keeps resetting as timestamps age out (a 100-page `iter_all` walk sustains ~rate/sec forever). Test: Task 1 `test_budget_resets_each_window_for_long_pulls`.
5. **Only 429 changes behaviour** — 401/404/422/5xx and transport errors behave exactly as before: one attempt, same exception types, no sleeps. Test: Task 4 `test_other_errors_are_not_retried`; plus the pre-existing `tests/test_errors.py` and `tests/test_client.py` suites re-run green in every task.

## File Structure

- **Create `src/simpro/ratelimit.py`** — pacing only: `DEFAULT_RATE_LIMIT`, the `RateLimiter` protocol, `SlidingWindowRateLimiter`. No HTTP knowledge.
- **Create `tests/test_ratelimit.py`** — unit tests for the limiter with a fake clock.
- **Create `tests/test_rate_limiting.py`** — client-level tests: pacing wiring, opt-out, shared limiters, 429 retry behaviour.
- **Modify `src/simpro/client.py`** — constructor kwargs (`rate_limit`, `rate_limiter`, later `max_retries`), pacing in `_request`, `_parse_retry_after`/`_retry_delay` helpers, 429 retry loop.
- **Modify `src/simpro/exceptions.py`** — `SimproRateLimitError.retry_after`.
- **Modify `src/simpro/__init__.py`** — export limiter names; version bump.
- **Modify `tests/test_errors.py`** — `status_client` kwargs pass-through; 429 tests updated when retry lands.
- **Modify `README.md`, `pyproject.toml`** — new "Rate limiting" section, corrected "Deliberate omissions", version `0.2.0`.

Responsibility boundaries: the limiter knows nothing about HTTP or SimPro; the client knows nothing about window maths. Resource modules (`resources/_base.py`, `customers.py`, etc.) are **not touched** — pacing is invisible below `_request`.

---

### Task 1: Sliding-window rate limiter

**Files:**
- Create: `src/simpro/ratelimit.py`
- Test: `tests/test_ratelimit.py`

**Interfaces:**
- Consumes: nothing (stdlib only).
- Produces (used by Tasks 2 and 5):
  - `DEFAULT_RATE_LIMIT: float = 9.0` in `simpro.ratelimit`
  - `RateLimiter` — a `typing.Protocol` with `acquire(self) -> None`
  - `SlidingWindowRateLimiter(rate_limit: float, *, timer: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep)` with method `acquire(self) -> None`; raises `ValueError` if `rate_limit <= 0`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ratelimit.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: FAIL at collection with `ModuleNotFoundError: No module named 'simpro.ratelimit'`

- [ ] **Step 3: Write the implementation**

Create `src/simpro/ratelimit.py`:

```python
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
```

Note the window maths: `max_events = max(1, int(rate_limit))` and `window = max_events / rate_limit`, so `max_events / window == rate_limit` exactly. `9.0` → 9 events per 1.0s; `0.5` → 1 event per 2.0s; `2.5` → 2 events per 0.8s.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_ratelimit.py -v`
Expected: 6 PASS

- [ ] **Step 5: Run lint and type checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all clean (fix any line-length/format nits with `uv run ruff format` first if flagged)

- [ ] **Step 6: Commit**

```bash
git add src/simpro/ratelimit.py tests/test_ratelimit.py
git commit -m "Add sliding-window rate limiter"
```

---

### Task 2: Client pacing wiring

**Files:**
- Modify: `src/simpro/client.py` (constructor lines 44-66 and `_request` lines 78-98)
- Modify: `src/simpro/__init__.py`
- Test: `tests/test_rate_limiting.py` (new file)

**Interfaces:**
- Consumes: `DEFAULT_RATE_LIMIT`, `RateLimiter`, `SlidingWindowRateLimiter` from Task 1 (exact signatures above).
- Produces (used by Tasks 4 and 5):
  - `SimproClient(base_url, api_key, *, timeout=30.0, transport=None, rate_limit: float | None = DEFAULT_RATE_LIMIT, rate_limiter: RateLimiter | None = None)`
  - `client._limiter: RateLimiter | None` — the limiter consulted before every request attempt (Task 4's retry loop re-consults it per attempt)
  - Package-level exports: `DEFAULT_RATE_LIMIT`, `RateLimiter`, `SlidingWindowRateLimiter`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rate_limiting.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rate_limiting.py -v`
Expected: FAIL with `ImportError: cannot import name 'DEFAULT_RATE_LIMIT' from 'simpro'` (the package does not export the limiter names yet)

- [ ] **Step 3: Wire the limiter into the client**

In `src/simpro/client.py`:

3a. Add the import (after the existing `from simpro._util import API_PREFIX` line, keeping ruff's isort order):

```python
from simpro.ratelimit import DEFAULT_RATE_LIMIT, RateLimiter, SlidingWindowRateLimiter
```

3b. Replace the `__init__` signature and the start of its body so it reads:

```python
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        rate_limit: float | None = DEFAULT_RATE_LIMIT,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        base = base_url.rstrip("/").removesuffix(API_PREFIX)
        self._base = base
        if rate_limiter is None and rate_limit is not None:
            rate_limiter = SlidingWindowRateLimiter(rate_limit)
        self._limiter = rate_limiter
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            transport=transport,
        )
```

(Everything from `# Resource namespaces.` down is unchanged.)

3c. Add these two entries to the class docstring's `Args:` block, after `transport`:

```
        rate_limit: Built-in pacing ceiling in requests/second. The
            limiter starts no more than this many requests in any sliding
            second. ``None`` disables pacing. Default 9.0 — just under
            SimPro's documented limit of 10/second per build and IP,
            which is shared across all integrations on the build.
        rate_limiter: Explicit limiter, overriding ``rate_limit``. Pass
            one shared instance to several clients pointed at the same
            build so they count against a single budget.
```

3d. In `_request`, add pacing as the first action after the docstring, before the `url = ...` line:

```python
        if self._limiter is not None:
            self._limiter.acquire()
```

(The `url = ...` line and everything after it is unchanged in this task.)

In `src/simpro/__init__.py`, add the import and exports:

```python
from simpro.client import SimproClient
from simpro.exceptions import (
    SimproAuthError,
    SimproError,
    SimproNotFound,
    SimproRateLimitError,
    SimproValidationError,
)
from simpro.ratelimit import DEFAULT_RATE_LIMIT, RateLimiter, SlidingWindowRateLimiter
```

```python
__all__ = [
    "DEFAULT_RATE_LIMIT",
    "RateLimiter",
    "SimproAuthError",
    "SimproClient",
    "SimproError",
    "SimproNotFound",
    "SimproRateLimitError",
    "SimproValidationError",
    "SlidingWindowRateLimiter",
    "__version__",
]
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_rate_limiting.py -v`
Expected: 6 PASS

- [ ] **Step 5: Run the full suite (regression: existing tests must not slow down or fail)**

Run: `uv run pytest -q`
Expected: all PASS, wall time under ~2s (existing tests make at most 5 requests per client, so default pacing never sleeps)

- [ ] **Step 6: Run lint and type checks**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all clean

- [ ] **Step 7: Commit**

```bash
git add src/simpro/client.py src/simpro/__init__.py tests/test_rate_limiting.py
git commit -m "Pace client requests inside SimPro's rate limit by default"
```

---

### Task 3: Parse `Retry-After` into `SimproRateLimitError.retry_after`

**Files:**
- Modify: `src/simpro/exceptions.py:28-29` (the `SimproRateLimitError` class)
- Modify: `src/simpro/client.py` (add `_parse_retry_after`; use it in `_raise_for_status`)
- Test: `tests/test_errors.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces (used by Task 4):
  - `SimproRateLimitError(message: str, *, retry_after: float | None = None, status_code: int | None = None)` with attribute `.retry_after`
  - `simpro.client._parse_retry_after(response: httpx.Response) -> float | None`

- [ ] **Step 1: Write the failing tests**

In `tests/test_errors.py`, append:

```python
def test_rate_limit_error_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    client = SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))
    with pytest.raises(SimproRateLimitError) as excinfo:
        client.companies.list()
    assert excinfo.value.retry_after == 7.0


def test_rate_limit_error_retry_after_defaults_to_none() -> None:
    with pytest.raises(SimproRateLimitError) as excinfo:
        status_client(429).companies.list()
    assert excinfo.value.retry_after is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_errors.py -k rate_limit -v`
Expected: both new tests FAIL with `AttributeError: 'SimproRateLimitError' object has no attribute 'retry_after'`

- [ ] **Step 3: Write the implementation**

In `src/simpro/exceptions.py`, replace the `SimproRateLimitError` class with:

```python
class SimproRateLimitError(SimproError):
    """SimPro rate limit exceeded (429).

    ``retry_after`` holds the ``Retry-After`` header parsed as seconds
    when SimPro sent one, otherwise ``None``.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.retry_after = retry_after
```

In `src/simpro/client.py`, change the 429 branch of `_raise_for_status` to:

```python
    if status == 429:
        raise SimproRateLimitError(
            "SimPro rate limit exceeded; retry after a pause",
            retry_after=_parse_retry_after(response),
            status_code=status,
        )
```

And add this module-level helper directly after `_raise_for_status`:

```python
def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse ``Retry-After`` as a delay in seconds; ``None`` when unusable.

    SimPro sends the delay-seconds form (``Retry-After: 5``). Anything
    else — absent header, HTTP-date form, garbage — degrades to ``None``
    so callers fall back to their own backoff.
    """
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_errors.py -v`
Expected: all PASS (including the pre-existing `test_rate_limit`)

- [ ] **Step 5: Run lint, types, and full suite**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all clean

- [ ] **Step 6: Commit**

```bash
git add src/simpro/exceptions.py src/simpro/client.py tests/test_errors.py
git commit -m "Parse Retry-After into SimproRateLimitError.retry_after"
```

---

### Task 4: Automatic 429 retry with `Retry-After` / backoff

**Files:**
- Modify: `src/simpro/client.py` (constructor, `_request`, new `_retry_delay`)
- Modify: `tests/test_errors.py` (keep old 429 tests fast under the new default)
- Test: `tests/test_rate_limiting.py` (append retry tests)

**Interfaces:**
- Consumes: `client._limiter` (Task 2), `_parse_retry_after` and `SimproRateLimitError.retry_after` (Task 3).
- Produces (used by Task 5 docs):
  - `SimproClient(..., max_retries: int = DEFAULT_MAX_RETRIES)` where `DEFAULT_MAX_RETRIES = 3`; `ValueError` if `max_retries < 0`
  - `simpro.client._sleep(seconds: float) -> None` — module-level sleep seam (tests monkeypatch `simpro.client._sleep`)
  - `simpro.client._retry_delay(response: httpx.Response, attempt: int) -> float`
  - Behaviour: `429` → up to `max_retries` retries; delay = `Retry-After` when usable (> 0), else `min(1.0 * 2**attempt, 30.0)`; retries exhausted → `SimproRateLimitError` via the existing `_raise_for_status`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_rate_limiting.py` (imports at top gain `SimproError`, `SimproRateLimitError` — full updated import block shown):

```python
from simpro import (
    DEFAULT_RATE_LIMIT,
    RateLimiter,
    SimproClient,
    SimproError,
    SimproRateLimitError,
    SlidingWindowRateLimiter,
)
```

Then append these tests:

```python
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


def test_429_retry_after_survives_to_final_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_rate_limiting.py -k "429 or max_retries or backoff or paced" -v`
Expected: FAIL with `TypeError: SimproClient.__init__() got an unexpected keyword argument 'max_retries'` (and monkeypatch `simpro.client._sleep` raising `AttributeError` for the seam)

- [ ] **Step 3: Implement the retry loop**

In `src/simpro/client.py`:

3a. Add the sleep seam import at the top (after `import httpx`):

```python
from time import sleep as _sleep
```

3b. Add the constant next to `DEFAULT_TIMEOUT = 30.0`:

```python
DEFAULT_MAX_RETRIES = 3
```

3c. Add `max_retries` to the constructor signature (after `rate_limiter`), store and validate it — the constructor body gains, right after the `self._limiter = rate_limiter` line:

```python
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries!r}")
        self._max_retries = max_retries
```

with signature now:

```python
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
        rate_limit: float | None = DEFAULT_RATE_LIMIT,
        rate_limiter: RateLimiter | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
```

3d. Add this entry to the class docstring `Args:` after `rate_limiter`:

```
        max_retries: Automatic retries when SimPro answers 429, honouring
            ``Retry-After`` when present (else exponential backoff from
            1s, capped at 30s). 0 disables retrying.
```

3e. Replace the whole `_request` method with:

```python
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """Perform one API request and map failures to typed exceptions.

        ``path`` is relative to the API prefix and starts with ``/``,
        e.g. ``/companies/0/customers/``. A 304 (If-Modified-Since hit) is
        returned to the caller as a normal response.

        Every attempt is paced by the client's rate limiter (if one is
        configured). A 429 answer is retried up to ``max_retries`` times
        — honouring ``Retry-After`` when SimPro sends it, otherwise
        exponential backoff — before ``SimproRateLimitError`` is raised.
        """
        url = f"{self._base}{API_PREFIX}{path}"
        attempts = self._max_retries + 1
        for attempt in range(attempts):
            if self._limiter is not None:
                self._limiter.acquire()
            try:
                response = self._http.request(method, url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise SimproError(f"SimPro request failed: {exc}") from exc
            if response.status_code != 429 or attempt == attempts - 1:
                break
            _sleep(_retry_delay(response, attempt))
        _raise_for_status(response)
        return response
```

3f. Add the module-level helper after `_parse_retry_after`:

```python
def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Wait before retrying a 429: Retry-After when usable, else backoff.

    The fallback starts at 1s and doubles per attempt, capped at 30s.
    """
    retry_after = _parse_retry_after(response)
    if retry_after is not None and retry_after > 0:
        return retry_after
    return min(1.0 * 2**attempt, 30.0)
```

- [ ] **Step 4: Keep the old 429 tests fast under the new default**

With retries on by default, a bare-429 test would sleep for real (1+2+4 seconds). In `tests/test_errors.py`:

4a. Change `status_client` to forward client kwargs (and add `Any` to the imports from `typing` — the file currently imports nothing from `typing`):

```python
from typing import Any
```

```python
def status_client(status: int, body: object | None = None, **client_kwargs: Any) -> SimproClient:
    def handler(request: httpx.Request) -> httpx.Response:
        kwargs: dict[str, object] = {}
        if body is not None:
            kwargs["json"] = body
        return httpx.Response(status, **kwargs)  # type: ignore[arg-type]

    client_kwargs.setdefault("transport", httpx.MockTransport(handler))
    return SimproClient("https://t.simprosuite.com", "k", **client_kwargs)
```

4b. Update `test_rate_limit` and `test_rate_limit_error_carries_retry_after` so their clients do not retry (final content of both):

```python
def test_rate_limit() -> None:
    with pytest.raises(SimproRateLimitError) as excinfo:
        status_client(429, max_retries=0).companies.list()
    assert excinfo.value.status_code == 429
    assert excinfo.value.retry_after is None
```

```python
def test_rate_limit_error_carries_retry_after() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "7"})

    client = SimproClient(
        "https://t.simprosuite.com",
        "k",
        transport=httpx.MockTransport(handler),
        max_retries=0,
    )
    with pytest.raises(SimproRateLimitError) as excinfo:
        client.companies.list()
    assert excinfo.value.retry_after == 7.0
```

- [ ] **Step 5: Run the rate-limiting tests to verify they pass**

Run: `uv run pytest tests/test_rate_limiting.py -v`
Expected: all PASS (17 total: 6 pacing + 11 retry)

- [ ] **Step 6: Run the full suite, lint, and types**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS, all clean, wall time under ~2s (no real sleeps anywhere)

- [ ] **Step 7: Commit**

```bash
git add src/simpro/client.py tests/test_rate_limiting.py tests/test_errors.py
git commit -m "Retry 429s automatically with Retry-After/backoff"
```

---

### Task 5: Documentation and version bump to 0.2.0

**Files:**
- Modify: `README.md`
- Modify: `src/simpro/client.py:1-11` (module docstring)
- Modify: `src/simpro/exceptions.py` (final `SimproRateLimitError` copy)
- Modify: `src/simpro/__init__.py:21` (version)
- Modify: `pyproject.toml:3` (version)

**Interfaces:**
- Consumes: final behaviour of Tasks 1-4 (`DEFAULT_RATE_LIMIT = 9.0`, `rate_limit`, `rate_limiter`, `max_retries = 3`, `.retry_after`).
- Produces: `simpro.__version__ == "0.2.0"` and `pyproject.toml` `version = "0.2.0"`.

- [ ] **Step 1: Update the client module docstring**

Replace the first paragraph's last two sentences in `src/simpro/client.py` ("The client is intentionally thin: no retry, no rate limiting, no caching. SimPro allows roughly 10 requests/second per key — callers with bulk pulls should pace requests and retry 429s/transport errors themselves.") so the docstring reads:

```python
"""The SimPro Cloud REST API client.

Every request URL is built in one place (``_request``) as
``{base}/api/v1.0{path}``; if ``base_url`` already ends with ``/api/v1.0``
the prefix is stripped first, so the well-known doubling trap (simPRO
answers 404 ``Invalid route``) is impossible by construction.

Requests are paced inside SimPro's rate limit — 10 calls/second per build
and IP, shared across all integrations — and 429 answers are retried with
backoff. See ``simpro.ratelimit`` for the limiter itself.
"""
```

- [ ] **Step 2: Finalise the `SimproRateLimitError` docstring**

In `src/simpro/exceptions.py`, replace the class docstring (implementation is unchanged from Task 3):

```python
class SimproRateLimitError(SimproError):
    """SimPro rate limit exceeded (429) after automatic retries ran out.

    The client already paces requests under SimPro's 10 calls/second
    limit and retries 429 answers (``max_retries``), so seeing this
    usually means other traffic on the build consumed the shared budget.
    ``retry_after`` holds the ``Retry-After`` header in seconds when
    SimPro sent one, otherwise ``None``.
    """
```

- [ ] **Step 3: Update the README**

3a. In the Errors table, change the 429 row to:

```markdown
| `SimproRateLimitError` | 429 after retries exhausted (`.retry_after` holds the parsed header, when sent) |
```

3b. Insert a new `## Rate limiting` section between `## Incremental pulls` and `## Errors`:

````markdown
## Rate limiting

SimPro enforces **10 API calls per second, per build and per IP** — a budget
shared across every integration and UI session hitting the build — and
answers over-limit traffic with `HTTP 429` (strictly enforced since April
2025).

The client keeps you inside that budget:

```python
client = SimproClient(base_url, api_key)                    # paced at 9 req/s
client = SimproClient(base_url, api_key, rate_limit=10)     # right at the ceiling
client = SimproClient(base_url, api_key, rate_limit=None)   # you handle pacing
```

- Pacing uses a sliding one-second window (default **9 requests/second**,
  just under the ceiling to leave headroom for other traffic on the shared
  budget) and adds no dependencies.
- The limit is per *build*, not per client: pass one shared limiter to
  several clients on the same tenant so they count against a single budget.

```python
from simpro import SlidingWindowRateLimiter

limiter = SlidingWindowRateLimiter(9)
a = SimproClient(base_url, key_a, rate_limiter=limiter)
b = SimproClient(base_url, key_b, rate_limiter=limiter)
```

- When SimPro still answers `429` (other integrations can consume the
  shared budget), the client retries automatically — up to `max_retries`
  times (default 3), honouring `Retry-After` when present, otherwise
  exponential backoff (1s, doubling, capped at 30s). Set `max_retries=0`
  to disable retrying.
````

3c. Replace the `## Deliberate omissions` section with:

```markdown
## Deliberate omissions

- **No retry on transport errors** — only `429` is retried automatically
  (see [Rate limiting](#rate-limiting)); connection resets and timeouts
  remain the caller's job.
- **No caching.**
- **No write endpoints** yet — read-only first.
- **No DTO layer** — responses are plain JSON dicts, so SDK releases never
  lag the API.
```

- [ ] **Step 4: Bump the version**

In `src/simpro/__init__.py`: `__version__ = "0.2.0"`
In `pyproject.toml`: `version = "0.2.0"`

- [ ] **Step 5: Verify everything**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: all PASS, all clean

Run: `uv run python -c "import simpro; assert simpro.__version__ == '0.2.0'; print(simpro.__version__)"`
Expected: prints `0.2.0`

- [ ] **Step 6: Commit**

```bash
git add README.md src/simpro/client.py src/simpro/exceptions.py src/simpro/__init__.py pyproject.toml
git commit -m "Document rate limiting and bump to 0.2.0"
```

---

## Design decisions (context for reviewers)

- **Why 9.0 default, not 10.0:** SimPro's forum announcement states the limit is "per IP and per build" and the developer docs say the *build's total* from all integrations must stay under 10/s. A library defaulting to exactly 10.0 would 429 whenever the customer's own UI or another integration touches the API concurrently. 9.0 leaves ~10% headroom; callers who want the full ceiling pass `rate_limit=10`.
- **Why sliding window, not fixed window or fixed interval:** a fixed 1s bucket permits a 20-request burst straddling a boundary (10 at the end of one bucket, 10 at the start of the next); a fixed minimum interval (0.1s) adds latency to every request. The sliding-window log matches the rule's own wording ("more than 10 requests per second" over a trailing window), lets a burst spend the whole budget instantly, then paces.
- **Why pacing holds the lock across sleeps:** two threads that both pass a window-fullness check would breach the budget together; serialising `acquire` makes waiting callers queue, which is exactly the pacing we want. An interrupted sleep (Ctrl-C) releases the lock via the `with` block.
- **Why only 429 is retried:** SimPro's guidance names 429 as the handle-at-client failure; retrying transport errors/5xx is a separate feature with idempotency questions, and 0.1.x explicitly scoped it out. This plan narrows the omission rather than expanding scope.
- **Why `rate_limit=None` opt-out exists:** restores exact pre-0.2 behaviour for callers with their own pacing infrastructure; the README documents it.

## Self-review record

- **Spec coverage:** user request "built in rate limiting" → Tasks 1-2; "stays within SimPro's defined rules" (10/s per build+IP, 429, Retry-After/backoff guidance) → Tasks 1-4; SimPro's shared-budget rule → default 9.0 + shared-limiter support; docs/version for a shipped library change → Task 5. No gaps.
- **Placeholders:** none — every step carries full code or exact commands with expected outcomes.
- **Type consistency:** `RateLimiter.acquire() -> None`, `SlidingWindowRateLimiter(rate_limit, *, timer, sleeper)`, `client._limiter`, `SimproRateLimitError(message, *, retry_after=None, status_code=None)`, `_parse_retry_after(response) -> float | None`, `_retry_delay(response, attempt) -> float`, `simpro.client._sleep`, `DEFAULT_MAX_RETRIES = 3` — names checked identical across Tasks 1-5.
- **Review Focus:** all five lines pinned to concrete tests (see section above).
