"""HTTP status → typed exception mapping."""

from __future__ import annotations

import httpx
import pytest

from simpro import (
    SimproAuthError,
    SimproClient,
    SimproError,
    SimproNotFound,
    SimproRateLimitError,
    SimproValidationError,
)


def status_client(status: int, body: object | None = None) -> SimproClient:
    def handler(request: httpx.Request) -> httpx.Response:
        kwargs: dict[str, object] = {}
        if body is not None:
            kwargs["json"] = body
        return httpx.Response(status, **kwargs)  # type: ignore[arg-type]

    return SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status", [401, 403])
def test_auth_errors(status: int) -> None:
    with pytest.raises(SimproAuthError) as excinfo:
        status_client(status).companies.list()
    assert excinfo.value.status_code == status


def test_not_found() -> None:
    with pytest.raises(SimproNotFound) as excinfo:
        status_client(404).jobs.get(0, 99)
    assert excinfo.value.status_code == 404
    assert "/jobs/99" in str(excinfo.value)


def test_rate_limit() -> None:
    with pytest.raises(SimproRateLimitError) as excinfo:
        status_client(429).companies.list()
    assert excinfo.value.status_code == 429


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


def test_422_with_errors_list() -> None:
    body = {
        "message": "The given data was invalid.",
        "errors": [
            {"path": "Name", "message": "Name is required", "value": None},
            {"path": "Email", "message": "Email is invalid", "value": "nope"},
        ],
    }
    with pytest.raises(SimproValidationError) as excinfo:
        status_client(422, body).companies.list()
    assert excinfo.value.details == body["errors"]
    assert "Name is required" in str(excinfo.value)
    assert "Email is invalid" in str(excinfo.value)


def test_422_with_absent_errors_key() -> None:
    body = {"message": "Something failed validation"}
    with pytest.raises(SimproValidationError) as excinfo:
        status_client(422, body).companies.list()
    assert excinfo.value.details == []
    assert "Something failed validation" in str(excinfo.value)


def test_422_with_non_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, text="<html>bad gateway html</html>")

    client = SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))
    with pytest.raises(SimproValidationError) as excinfo:
        client.companies.list()
    assert excinfo.value.details == []
    assert "bad gateway html" in str(excinfo.value)


def test_other_statuses_raise_base_simpro_error() -> None:
    with pytest.raises(SimproError) as excinfo:
        status_client(500, {"oops": True}).companies.list()
    assert type(excinfo.value) is SimproError
    assert excinfo.value.status_code == 500


def test_exception_hierarchy() -> None:
    assert issubclass(SimproAuthError, SimproError)
    assert issubclass(SimproNotFound, SimproError)
    assert issubclass(SimproRateLimitError, SimproError)
    assert issubclass(SimproValidationError, SimproError)
