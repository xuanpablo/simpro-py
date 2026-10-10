"""Customers endpoints: ``GET /api/v1.0/companies/{companyID}/customers/``."""

from __future__ import annotations

from typing import Any

from simpro.exceptions import SimproNotFound
from simpro.resources._base import CompanyScopedResource


class Customers(CompanyScopedResource):
    """Customers (company and individual)."""

    _resource = "customers"

    def get(self, company_id: int, customer_id: int) -> dict[str, Any]:
        """Fetch one customer by ID.

        SimPro splits single-customer GETs by type: company customers live at
        ``/customers/companies/{ID}`` and individuals at
        ``/customers/individuals/{ID}``. This method tries the company path
        first and falls back to individuals, so callers need not know the
        type up front.
        """
        try:
            response = self._client._request(
                "GET", f"/companies/{company_id}/customers/companies/{customer_id}"
            )
        except SimproNotFound:
            response = self._client._request(
                "GET", f"/companies/{company_id}/customers/individuals/{customer_id}"
            )
        data: Any = response.json()
        return data
