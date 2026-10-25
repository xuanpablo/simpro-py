"""Quotes endpoints: ``GET /api/v1.0/companies/{companyID}/quotes/``.

The ``Leads`` twin: list items are minimal, so callers project the fields
they need through the ``columns`` csv query param, which passes through
``**filters`` verbatim — e.g.
``client.quotes.list(0, columns="ID,Name,Stage,Total")``.
"""

from __future__ import annotations

from simpro.resources._base import CompanyScopedResource


class Quotes(CompanyScopedResource):
    """Quotes (pre-sale priced offers)."""

    _resource = "quotes"
