"""Jobs endpoints: ``GET /api/v1.0/companies/{companyID}/jobs/``."""

from __future__ import annotations

from simpro.resources._base import CompanyScopedResource


class Jobs(CompanyScopedResource):
    """Jobs (work orders / projects)."""

    _resource = "jobs"
