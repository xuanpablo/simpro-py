"""Companies endpoints: ``GET /api/v1.0/companies/``."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from simpro._util import format_http_date
from simpro.resources._base import Resource


class Companies(Resource):
    """The companies the API key can see (usually exactly one)."""

    def list(
        self,
        *,
        search: str | None = None,
        modified_since: datetime | None = None,
        **filters: Any,
    ) -> list[dict[str, Any]]:
        """List companies.

        SimPro does not document ``page``/``pageSize`` for this endpoint; the
        response is a single unpaginated array. ``**filters`` and
        ``modified_since`` behave as on other resources.
        """
        params: dict[str, Any] = dict(filters)
        if search is not None:
            params["search"] = search
        headers: dict[str, str] | None = None
        if modified_since is not None:
            headers = {"If-Modified-Since": format_http_date(modified_since)}
        response = self._client._request("GET", "/companies/", params=params, headers=headers)
        if response.status_code == 304:
            return []
        data: Any = response.json()
        return data if isinstance(data, list) else [data]

    def get(self, company_id: int) -> dict[str, Any]:
        """Fetch one company by ID."""
        response = self._client._request("GET", f"/companies/{company_id}")
        data: Any = response.json()
        return data
