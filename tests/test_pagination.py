"""Pagination walk behaviour: Result-Pages header + short-page fallback."""

from __future__ import annotations

from typing import Any

import httpx

from simpro import SimproClient


def paged_handler(pages: dict[str, list[dict[str, int]]], requests: list[httpx.Request]) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get("page", "1")
        body = pages.get(page, [])
        return httpx.Response(
            200,
            json=body,
            headers={"Result-Total": "5", "Result-Pages": str(len(pages))},
        )

    return handler


def test_iter_all_walks_all_pages_via_result_pages_header() -> None:
    requests: list[httpx.Request] = []
    pages = {
        "1": [{"ID": 1}, {"ID": 2}],
        "2": [{"ID": 3}],
    }
    client = SimproClient(
        "https://t.simprosuite.com",
        "k",
        transport=httpx.MockTransport(paged_handler(pages, requests)),
    )
    ids = [c["ID"] for c in client.customers.iter_all(0, per_page=2)]
    assert ids == [1, 2, 3]
    # Exactly two page requests — the Result-Pages header stops the walk.
    assert [r.url.params["page"] for r in requests] == ["1", "2"]


def test_iter_all_short_page_fallback_without_header() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get("page", "1")
        # Page 1 full (2 items), page 2 short (1 item) — no Result-Pages header.
        body = [{"ID": 1}, {"ID": 2}] if page == "1" else [{"ID": 3}]
        return httpx.Response(200, json=body)

    client = SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))
    ids = [c["ID"] for c in client.customers.iter_all(0, per_page=2)]
    assert ids == [1, 2, 3]
    assert len(requests) == 2


def test_iter_all_stops_on_exact_full_page_then_empty() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        page = request.url.params.get("page", "1")
        body = [{"ID": 1}, {"ID": 2}] if page == "1" else []
        return httpx.Response(200, json=body)

    client = SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))
    ids = [c["ID"] for c in client.customers.iter_all(0, per_page=2)]
    assert ids == [1, 2]
    assert len(requests) == 2


def test_list_304_yields_empty_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(304)

    client = SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))
    assert client.sites.list(0) == []
    assert list(client.jobs.iter_all(0)) == []


def test_iter_all_garbage_result_pages_falls_back_to_short_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = request.url.params.get("page", "1")
        body = [{"ID": 1}] if page == "1" else []
        return httpx.Response(200, json=body, headers={"Result-Pages": "not-a-number"})

    client = SimproClient("https://t.simprosuite.com", "k", transport=httpx.MockTransport(handler))
    assert [c["ID"] for c in client.customers.iter_all(0, per_page=5)] == [1]
