"""Client-core behaviour: auth header, URL joining, modified_since."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from simpro import SimproClient, SimproError


def make_client(handler: Any, base_url: str = "https://t.simprosuite.com") -> SimproClient:
    return SimproClient(base_url, "key-123", transport=httpx.MockTransport(handler))


def full_url(request: httpx.Request) -> str:
    """Request URL without query params, for exact-path assertions."""
    return f"{request.url.scheme}://{request.url.host}{request.url.path}"


def recorded(requests: list[httpx.Request]) -> Any:
    """Handler that records requests and answers 200 with an empty array."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    return handler


def test_bearer_header_on_every_request() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    client.companies.list()
    client.customers.list(0)
    client.customers.get(0, 7)
    client.sites.list(0)
    client.jobs.get(0, 42)
    assert len(requests) == 5
    assert all(r.headers["Authorization"] == "Bearer key-123" for r in requests)


def test_url_joining_single_prefix() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    client.customers.list(0)
    assert full_url(requests[0]) == "https://t.simprosuite.com/api/v1.0/companies/0/customers/"


def test_base_url_trailing_slash_tolerated() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests), "https://t.simprosuite.com/")
    client.companies.get(0)
    assert full_url(requests[0]) == "https://t.simprosuite.com/api/v1.0/companies/0"


def test_base_url_with_api_prefix_is_stripped_no_doubling() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests), "https://t.simprosuite.com/api/v1.0/")
    client.jobs.list(0)
    assert full_url(requests[0]) == "https://t.simprosuite.com/api/v1.0/companies/0/jobs/"


def test_pagination_params_are_page_and_page_size() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    client.sites.list(0, page=2, per_page=100)
    url = requests[0].url
    assert url.params["page"] == "2"
    assert url.params["pageSize"] == "100"


def test_per_page_clamped_to_250() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    client.customers.list(0, per_page=10_000)
    assert requests[0].url.params["pageSize"] == "250"


def test_modified_since_sent_as_rfc1123_header() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    client.customers.list(0, modified_since=datetime(2026, 3, 1, 12, 30, 5, tzinfo=UTC))
    assert requests[0].headers["If-Modified-Since"] == "Sun, 01 Mar 2026 12:30:05 GMT"


def test_modified_since_naive_datetime_treated_as_utc() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    client.customers.list(0, modified_since=datetime(2026, 3, 1, 12, 30, 5))
    assert requests[0].headers["If-Modified-Since"] == "Sun, 01 Mar 2026 12:30:05 GMT"


def test_modified_since_offset_datetime_normalised_to_utc() -> None:
    import zoneinfo

    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    melbourne = zoneinfo.ZoneInfo("Australia/Melbourne")
    client.customers.list(0, modified_since=datetime(2026, 3, 1, 23, 30, 5, tzinfo=melbourne))
    # 23:30 AEDT (UTC+11) == 12:30 UTC
    assert requests[0].headers["If-Modified-Since"] == "Sun, 01 Mar 2026 12:30:05 GMT"


def test_search_and_filters_passed_verbatim() -> None:
    requests: list[httpx.Request] = []
    client = make_client(recorded(requests))
    client.customers.list(0, search="any", Name="Acme")
    assert requests[0].url.params["search"] == "any"
    assert requests[0].url.params["Name"] == "Acme"


def test_transport_errors_raise_simpro_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = make_client(handler)
    with pytest.raises(SimproError) as excinfo:
        client.companies.list()
    # Must be SimproError, not a bare httpx error escaping the SDK.
    assert excinfo.value.status_code is None
