"""Exception hierarchy for the simpro client.

Every HTTP failure is mapped to one of these types in one place
(``simpro.client``); transport-level failures raise ``SimproError``.
"""

from __future__ import annotations

from typing import Any


class SimproError(Exception):
    """Base error for all simpro failures (transport or HTTP)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SimproAuthError(SimproError):
    """SimPro rejected the request as unauthenticated or unauthorised (401/403)."""


class SimproNotFound(SimproError):
    """The requested SimPro resource does not exist (404)."""


class SimproRateLimitError(SimproError):
    """SimPro rate limit exceeded (429). Retry after a pause; the SDK does not retry."""


class SimproValidationError(SimproError):
    """SimPro rejected the request payload (422).

    ``details`` carries the raw error list when the response body provides
    one: SimPro returns ``{"errors": [{"path", "message", "value"}, ...]}``
    but the ``errors`` key may be absent, in which case ``details`` is empty
    and the message carries whatever the body offered.
    """

    def __init__(
        self,
        message: str,
        *,
        details: list[dict[str, Any]] | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message, status_code=status_code)
        self.details = details if details is not None else []
