"""Resource namespace base classes.

``CompanyScopedResource`` implements the shared GET/list/iter_all shape for
resources under ``/companies/{companyID}/<resource>/``. Pagination follows
the SimPro convention: ``page`` + ``pageSize`` query params, list bodies are
plain JSON arrays, and the continuation signal is the ``Result-Pages``
response header (total page count). When the header is absent the walk falls
back to stopping on a short page.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any

from simpro._util import MAX_PAGE_SIZE, format_http_date

if TYPE_CHECKING:
    from simpro.client import SimproClient


class Resource:
    """A resource namespace attached to a :class:`~simpro.SimproClient`."""

    def __init__(self, client: SimproClient) -> None:
        self._client = client


class CompanyScopedResource(Resource):
    """GET-only resource under ``/companies/{companyID}/``."""

    _resource: str

    def list(
        self,
        company_id: int,
        *,
        search: str | None = None,
        page: int = 1,
        per_page: int = 250,
        modified_since: datetime | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """Fetch one page of results.

        Args:
            company_id: SimPro company ID (0 unless multi-company).
            search: Verbatim ``search`` query param — SimPro's documented
                values are ``"all"``/``"any"`` (match mode for the field
                filters passed via ``**filters``).
            page: 1-based page number.
            per_page: Page size, clamped to SimPro's max of 250.
            modified_since: Watermark for list endpoints — sent as an
                ``If-Modified-Since`` header; a 304 answer yields ``[]``.
            **filters: Extra query params, passed through verbatim
                (e.g. ``Name="Acme"``).
        """
        response = self._list_page(
            company_id,
            page=page,
            per_page=per_page,
            search=search,
            modified_since=modified_since,
            **filters,
        )
        if response.status_code == 304:
            return []
        data: Any = response.json()
        return data if isinstance(data, list) else [data]

    def get(self, company_id: int, resource_id: int) -> dict[str, Any]:
        """Fetch one record by ID."""
        response = self._client._request(
            "GET", f"/companies/{company_id}/{self._resource}/{resource_id}"
        )
        data: Any = response.json()
        return data

    def iter_all(
        self,
        company_id: int,
        *,
        per_page: int = MAX_PAGE_SIZE,
        modified_since: datetime | None = None,
        **filters: Any,
    ) -> Iterator[dict[str, Any]]:
        """Yield every record, walking pages until exhausted.

        Stops when the page number reaches the ``Result-Pages`` response
        header, or — if that header is missing — when a page comes back
        shorter than ``per_page``.
        """
        page_size = _clamp_page_size(per_page)
        page = 1
        while True:
            response = self._list_page(
                company_id,
                page=page,
                per_page=page_size,
                search=None,
                modified_since=modified_since,
                **filters,
            )
            if response.status_code == 304:
                return
            data: Any = response.json()
            items = data if isinstance(data, list) else [data]
            yield from items
            if not _has_more_pages(response, page, len(items), page_size):
                return
            page += 1

    def _list_page(
        self,
        company_id: int,
        *,
        page: int,
        per_page: int,
        search: str | None,
        modified_since: datetime | None,
        **filters: Any,
    ) -> Any:
        params: dict[str, Any] = {
            "page": page,
            "pageSize": _clamp_page_size(per_page),
        }
        if search is not None:
            params["search"] = search
        params.update(filters)
        headers: dict[str, str] | None = None
        if modified_since is not None:
            headers = {"If-Modified-Since": format_http_date(modified_since)}
        return self._client._request(
            "GET",
            f"/companies/{company_id}/{self._resource}/",
            params=params,
            headers=headers,
        )


def _clamp_page_size(per_page: int) -> int:
    return max(1, min(per_page, MAX_PAGE_SIZE))


def _has_more_pages(response: Any, page: int, items_on_page: int, page_size: int) -> bool:
    raw = response.headers.get("Result-Pages")
    if raw is not None:
        try:
            total_pages = int(raw)
        except ValueError:
            total_pages = 0
        if total_pages > 0:
            return page < total_pages
    return items_on_page >= page_size
