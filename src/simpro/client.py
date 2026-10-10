"""The SimPro Cloud REST API client.

Every request URL is built in one place (``_request``) as
``{base}/api/v1.0{path}``; if ``base_url`` already ends with ``/api/v1.0``
the prefix is stripped first, so the well-known doubling trap (simPRO
answers 404 ``Invalid route``) is impossible by construction.

The client is intentionally thin: no retry, no rate limiting, no caching.
SimPro allows roughly 10 requests/second per key — callers with bulk pulls
should pace requests and retry 429s/transport errors themselves.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from simpro.resources import Companies, Customers, Jobs, Sites

DEFAULT_TIMEOUT = 30.0


class SimproClient:
    """Synchronous client for the SimPro Cloud REST API (v1.0).

    Args:
        base_url: Tenant root, e.g. ``https://yourtenant.simprosuite.com``.
            A trailing ``/api/v1.0`` is tolerated and stripped.
        api_key: API key bearer token (SimPro > Administration > API Keys).
        timeout: Per-request timeout in seconds.
        transport: Optional ``httpx`` transport override (testing/proxies).
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        base = base_url.rstrip("/").removesuffix(API_PREFIX)
        self._base = base
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
    ) -> httpx.Response:
        """Perform one API request and map failures to typed exceptions.

        ``path`` is relative to the API prefix and starts with ``/``,
        e.g. ``/companies/0/customers/``. A 304 (If-Modified-Since hit) is
        returned to the caller as a normal response.
        """
        url = f"{self._base}{API_PREFIX}{path}"
        try:
            response = self._http.request(method, url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise SimproError(f"SimPro request failed: {exc}") from exc
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
            "SimPro rate limit exceeded; retry after a pause", status_code=status
        )
    if status == 422:
        message, details = _parse_validation_body(response)
        raise SimproValidationError(message, details=details, status_code=status)
    raise SimproError(
        f"SimPro request failed with HTTP {status}: {response.text[:200]}",
        status_code=status,
    )


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
