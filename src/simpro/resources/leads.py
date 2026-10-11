"""Leads endpoints: ``GET /api/v1.0/companies/{companyID}/leads/``.

List items are minimal (``ID`` + nested ``Customer``/``Site`` objects);
extra fields (``LeadName``, ``Stage``, dates, ``Forecast``…) come via the
``columns`` csv query param — e.g. ``client.leads.list(0, columns="ID,LeadName,Stage")``.
"""

from __future__ import annotations

from simpro.resources._base import CompanyScopedResource


class Leads(CompanyScopedResource):
    """Leads (pre-sale requests for quote)."""

    _resource = "leads"
