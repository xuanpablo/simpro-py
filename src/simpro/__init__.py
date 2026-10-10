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

__version__ = "0.1.0"

__all__ = [
    "SimproAuthError",
    "SimproClient",
    "SimproError",
    "SimproNotFound",
    "SimproRateLimitError",
    "SimproValidationError",
    "__version__",
]
