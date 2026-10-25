"""Resource namespaces attached to :class:`~simpro.SimproClient`."""

from simpro.resources.companies import Companies
from simpro.resources.customers import Customers
from simpro.resources.jobs import Jobs
from simpro.resources.leads import Leads
from simpro.resources.quotes import Quotes
from simpro.resources.setup_webhooks import Webhooks
from simpro.resources.sites import Sites

__all__ = ["Companies", "Customers", "Jobs", "Leads", "Quotes", "Sites", "Webhooks"]
