"""Internal helpers shared by the client and the resource modules."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime

API_PREFIX = "/api/v1.0"
MAX_PAGE_SIZE = 250


def format_http_date(dt: datetime) -> str:
    """Format ``dt`` as an RFC 1123 date string (UTC), for ``If-Modified-Since``.

    Naive datetimes are treated as UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return format_datetime(dt.astimezone(UTC), usegmt=True)
