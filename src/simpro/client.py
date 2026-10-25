"""The SimPro Cloud REST API client.

Every request URL is built in one place (``_request``) as
``{base}/api/v1.0{path}``; if ``base_url`` already ends with ``/api/v1.0``
the prefix is stripped first, so the well-known doubling trap (simPRO
answers 404 ``Invalid route``) is impossible by construction.

Requests are paced inside SimPro's rate limit — 10 calls/second per build
and IP, shared across all integrations — and 429 answers are retried with
backoff. See ``simpro.ratelimit`` for the limiter itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from time import sleep as _sleep
from typing import Any, Self

import httpx

from simpro._util import API_PREFIX
from simpro.exceptions import (
    SimproAuthError,
    SimproError,
    SimproNotFound,
    SimproRateLimitError,
    SimproValidationError,
)
from simpro.ratelimit import DEFAULT_RATE_LIMIT, RateLimiter, SlidingWindowRateLimiter
from simpro.resources import Companies, Customers, Jobs, Leads, Quotes, Sites, Webhooks

DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 3


class SimproClient:
    """Synchronous client for the SimPro Cloud REST API (v1.0).

    Args:
        base_url: Tenant root, e.g. ``https://yourtenant.simprosuite.com``.
            A trailing ``/api/v1.0`` is tolerated and stripped.
        api_key: API key bearer token (SimPro > Administration > API Keys).
        timeout: Per-request timeout in seconds.
        transport: Optional ``httpx`` transport override (testing/proxies).
        rate_limit: Built-in pacing ceiling in requests/second. The
            limiter starts no more than this many requests in any sliding
            second. ``None`` disables pacing. Default 9.0 — just under
            SimPro's documented limit of 10/second per build and IP,
            which is shared across all integrations on the build.
        rate_limiter: Explicit limiter, overriding ``rate_limit``. Pass
            one shared instance to several clients pointed at the same
            build so they count against a single budget.
        max_retries: Automatic retries when SimPro answers 429, honouring
            ``Retry-After`` when present (else exponential backoff from
            1s, capped at 30s). 0 disables retrying.
    """

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
        base = base_url.rstrip("/").removesuffix(API_PREFIX)
        self._base = base
        if rate_limiter is None and rate_limit is not None:
            rate_limiter = SlidingWindowRateLimiter(rate_limit)
        self._limiter = rate_limiter
        if max_retries < 0:
            raise ValueError(f"max_retries must be >= 0, got {max_retries!r}")
        self._max_retries = max_retries
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
            transport=transport,
        )
        # Resource namespaces.
        self.companies = Companies(self)
        self.customers = Customers(self)
        self.sites = Sites(self)
        self.jobs = Jobs(self)
        self.leads = Leads(self)
        self.quotes = Quotes(self)
        self.webhooks = Webhooks(self)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._http.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        """Perform one API request and map failures to typed exceptions.

        ``path`` is relative to the API prefix and starts with ``/``,
        e.g. ``/companies/0/customers/``. A 304 (If-Modified-Since hit) is
        returned to the caller as a normal response. ``json`` is serialised
        as the request body when given (POST/PATCH).

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
                response = self._http.request(
                    method, url, params=params, headers=headers, json=json
                )
            except httpx.HTTPError as exc:
                raise SimproError(f"SimPro request failed: {exc}") from exc
            if response.status_code != 429 or attempt == attempts - 1:
                break
            _sleep(_retry_delay(response, attempt))
        _raise_for_status(response)
        return response


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400 or status == 304:
        return
    if status in (401, 403):
        raise SimproAuthError(
            f"SimPro rejected the credentials (HTTP {status})", status_code=status
        )
    if status == 404:
        raise SimproNotFound(f"SimPro resource not found: {response.url}", status_code=status)
    if status == 429:
        raise SimproRateLimitError(
            "SimPro rate limit exceeded; retry after a pause",
            retry_after=_parse_retry_after(response),
            status_code=status,
        )
    if status == 422:
        message, details = _parse_validation_body(response)
        raise SimproValidationError(message, details=details, status_code=status)
    raise SimproError(
        f"SimPro request failed with HTTP {status}: {response.text[:200]}",
        status_code=status,
    )


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


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Wait before retrying a 429: Retry-After when usable, else backoff.

    The fallback starts at 1s and doubles per attempt, capped at 30s.
    """
    retry_after = _parse_retry_after(response)
    if retry_after is not None and retry_after > 0:
        return retry_after
    return min(1.0 * 2**attempt, 30.0)


def _parse_validation_body(
    response: httpx.Response,
) -> tuple[str, list[dict[str, Any]]]:
    """Extract a message and error details from a 422 body.

    SimPro 422 bodies are ``{"errors": [{"path", "message", "value"}, ...]}``
    — but the ``errors`` key may be absent, and the body may not be JSON at
    all. Both shapes degrade gracefully.
    """
    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        errors = body.get("errors")
        details = [e for e in errors if isinstance(e, dict)] if isinstance(errors, list) else []
        message = body.get("message") if isinstance(body.get("message"), str) else None
        if message is None:
            message = "SimPro rejected the request payload (HTTP 422)"
        if details:
            summary = "; ".join(
                f"{d.get('path', '?')}: {d.get('message', '')}" for d in details[:5]
            )
            message = f"{message} — {summary}"
        return message, details
    if isinstance(body, list):
        details = [e for e in body if isinstance(e, dict)]
        return "SimPro rejected the request payload (HTTP 422)", details
    return f"SimPro rejected the request payload (HTTP 422): {response.text[:200]}", []
