"""Sites endpoints: ``GET /api/v1.0/companies/{companyID}/sites/``."""

from __future__ import annotations

from simpro.resources._base import CompanyScopedResource


class Sites(CompanyScopedResource):
    """Sites (addresses where work is performed)."""

    _resource = "sites"
