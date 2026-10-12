"""Resource URL shapes: companies, customers (with type fallback), sites, jobs."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from simpro import SimproClient, SimproNotFound


def make_client(handler: Any) -> SimproClient:
    return SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))


def full_url(request: httpx.Request) -> str:
    """Request URL without query params, for exact-path assertions."""
    return f"{request.url.scheme}://{request.url.host}{request.url.path}"


def test_companies_list_and_get_urls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        company = {"ID": 0, "Name": "Millar Electrics"}
        # list ends with "/companies/", single get with "/companies/0"
        if request.url.path.endswith("/companies/0"):
            return httpx.Response(200, json=company)
        return httpx.Response(200, json=[company])

    client = make_client(handler)
    assert client.companies.list() == [{"ID": 0, "Name": "Millar Electrics"}]
    assert full_url(requests[0]) == "https://t.simprosuite.com/api/v1.0/companies/"
    assert client.companies.get(0) == {"ID": 0, "Name": "Millar Electrics"}
    assert full_url(requests[1]) == "https://t.simprosuite.com/api/v1.0/companies/0"


def test_companies_list_sends_no_pagination_params() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    make_client(handler).companies.list()
    assert len(requests[0].url.params) == 0


def test_customer_get_company_first() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ID": 7, "CompanyName": "Acme Pty Ltd"})

    client = make_client(handler)
    result = client.customers.get(0, 7)
    assert result["CompanyName"] == "Acme Pty Ltd"
    assert (
        str(requests[0].url)
        == "https://t.simprosuite.com/api/v1.0/companies/0/customers/companies/7"
    )
    assert len(requests) == 1


def test_customer_get_falls_back_to_individuals_on_404() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/customers/companies/7"):
            return httpx.Response(404, json={"message": "Not found"})
        return httpx.Response(200, json={"ID": 7, "GivenName": "Jane", "FamilyName": "Doe"})

    client = make_client(handler)
    result = client.customers.get(0, 7)
    assert result["GivenName"] == "Jane"
    assert (
        str(requests[1].url)
        == "https://t.simprosuite.com/api/v1.0/companies/0/customers/individuals/7"
    )


def test_customer_get_missing_everywhere_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not found"})

    with pytest.raises(SimproNotFound):
        make_client(handler).customers.get(0, 7)


def test_sites_and_jobs_get_urls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ID": 1})

    client = make_client(handler)
    client.sites.get(0, 12)
    client.jobs.get(0, 34)
    assert str(requests[0].url) == "https://t.simprosuite.com/api/v1.0/companies/0/sites/12"
    assert full_url(requests[1]) == "https://t.simprosuite.com/api/v1.0/companies/0/jobs/34"


def test_sites_and_jobs_list_urls() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    client = make_client(handler)
    client.sites.list(0)
    client.jobs.list(0)
    assert full_url(requests[0]) == "https://t.simprosuite.com/api/v1.0/companies/0/sites/"
    assert full_url(requests[1]) == "https://t.simprosuite.com/api/v1.0/companies/0/jobs/"


def test_leads_list_url_and_columns_filter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[])

    client = make_client(handler)
    client.leads.list(0, columns="ID,LeadName,Stage")
    assert full_url(requests[0]) == "https://t.simprosuite.com/api/v1.0/companies/0/leads/"
    assert requests[0].url.params["columns"] == "ID,LeadName,Stage"


def test_leads_get_url() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ID": 5, "Stage": "Open"})

    client = make_client(handler)
    assert client.leads.get(0, 5)["Stage"] == "Open"
    assert full_url(requests[0]) == "https://t.simprosuite.com/api/v1.0/companies/0/leads/5"


def test_leads_iter_all_walks_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        body = [{"ID": 1}, {"ID": 2}] if page == "1" else [{"ID": 3}]
        return httpx.Response(200, json=body, headers={"Result-Pages": "2"})

    client = SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))
    assert [lead["ID"] for lead in client.leads.iter_all(0)] == [1, 2, 3]
