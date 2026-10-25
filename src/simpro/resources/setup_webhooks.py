"""Webhook subscriptions: ``/api/v1.0/companies/{companyID}/setup/webhooks/``.

Subscriptions are fully API-managed per build/company, so an integration
registers its own callback instead of asking a tenant admin to.

A subscription body carries ``Name``, ``CallbackURL``, ``Secret``,
``Email``, ``Description``, ``Events`` (e.g. ``["lead.created"]``),
``Status`` and ``DateCreated``. ``ID`` comes back on create and is what
``update``/``delete`` take.
"""

from __future__ import annotations

from simpro.resources._base import WritableCompanyScopedResource


class Webhooks(WritableCompanyScopedResource):
    """Webhook subscriptions for this company."""

    _resource = "setup/webhooks"
