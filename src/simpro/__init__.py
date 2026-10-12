"""simpro-py: a thin Python client for the SimPro Cloud REST API (v1.0).

Quickstart::

    from simpro import SimproClient

    client = SimproClient("https://yourtenant.simprosuite.com", "API_KEY")
    for customer in client.customers.iter_all(0):
        print(customer["ID"], customer.get("CompanyName"))
"""

from simpro.client import SimproClient
from simpro.exceptions import (
    SimproAuthError,
    SimproError,
    SimproNotFound,
    SimproRateLimitError,
    SimproValidationError,
)
from simpro.ratelimit import DEFAULT_RATE_LIMIT, RateLimiter, SlidingWindowRateLimiter

__version__ = "0.3.0"

__all__ = [
    "DEFAULT_RATE_LIMIT",
    "RateLimiter",
    "SimproAuthError",
    "SimproClient",
    "SimproError",
    "SimproNotFound",
    "SimproRateLimitError",
    "SimproValidationError",
    "SlidingWindowRateLimiter",
    "__version__",
]
