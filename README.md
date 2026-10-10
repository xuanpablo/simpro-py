# simpro-py

A thin Python client for the [SimPro Cloud REST API (v1.0)](https://www.simprogroup.com).

```python
from simpro import SimproClient

client = SimproClient("https://yourtenant.simprosuite.com", "YOUR_API_KEY")

for customer in client.customers.iter_all(0):
    print(customer["ID"], customer.get("CompanyName") or customer.get("FamilyName"))
```

The distribution is `simpro-py-sdk`; the import name is `simpro`.

## Install

```bash
pip install simpro-py-sdk
```

Requires Python 3.12+. The only runtime dependency is `httpx`.

## Authentication

SimPro API keys are bearer tokens — create one in SimPro under
**Administration > API Keys** and pass it with your tenant base URL:

```python
client = SimproClient("https://yourtenant.simprosuite.com", "YOUR_API_KEY")
```

Every request is sent to `{base_url}/api/v1.0/...`. If your `base_url` already
ends with `/api/v1.0` it is stripped automatically, so the well-known
doubled-prefix 404 (`Invalid route`) cannot happen.

## Resources

0.1.x ships read-only access to the first-slice resources, one namespace per
resource, hung off the client:

| Namespace | Endpoints |
|---|---|
| `client.companies` | `list(*, search=None, modified_since=None, **filters)`, `get(company_id)` |
| `client.customers` | `list(company_id, ...)`, `get(company_id, customer_id)`, `iter_all(company_id, **filters)` |
| `client.sites` | `list(company_id, ...)`, `get(company_id, site_id)`, `iter_all(company_id, **filters)` |
| `client.jobs` | `list(company_id, ...)`, `get(company_id, job_id)`, `iter_all(company_id, **filters)` |

`company_id` is `0` unless your build uses SimPro multi-company.

Notes:

- **Single-customer GETs are type-split in SimPro** (company vs individual).
  `client.customers.get(company_id, customer_id)` tries the company path first
  and falls back to individuals, so callers never need the type up front.
- **`search`** is SimPro's documented match-mode param (`"all"`/`"any"`) that
  combines with field filters. Field filters themselves are passed verbatim as
  extra keyword arguments, e.g. `client.customers.list(0, Name="Acme")`.
- All methods return plain dicts/lists — no DTO layer by design.

## Pagination

`list()` fetches a single page (`page`, `per_page`, capped at SimPro's maximum
of 250). `iter_all()` walks every page for you:

```python
for job in client.jobs.iter_all(0):
    ...
```

Page continuation follows the SimPro convention: the `Result-Pages` response
header carries the total page count; if it is absent, the walk stops on a
short page.

## Incremental pulls

List endpoints honour the `If-Modified-Since` watermark. Pass
`modified_since=<datetime>` and the client formats the header (RFC 1123, UTC)
and maps a `304 Not Modified` answer to an empty result:

```python
since = last_sync_run_at  # aware datetime; naive values are treated as UTC
new_customers = client.customers.list(0, modified_since=since)
```

## Errors

All failures raise a subclass of `SimproError`:

| Exception | HTTP status |
|---|---|
| `SimproAuthError` | 401, 403 |
| `SimproNotFound` | 404 |
| `SimproValidationError` | 422 (`.details` holds the raw error list) |
| `SimproRateLimitError` | 429 |
| `SimproError` | anything else, including transport failures |

SimPro 422 bodies are normally `{"errors": [{"path", "message", "value"}, ...]}`
but the `errors` key may be absent — both shapes are handled, and
`SimproValidationError.details` is always a list.

## Deliberate omissions

- **No retry, no rate limiting, no caching.** SimPro allows roughly 10
  requests/second per key; callers doing bulk pulls should pace requests and
  retry 429s/transport errors themselves.
- **No write endpoints** in 0.1.x — read-only first.
- **No DTO layer** — responses are plain JSON dicts, so SDK releases never
  lag the API.

## Lineage

This package is a clean rewrite of [`limx0/simpro-py`](https://github.com/limx0/simpro-py)
(MIT), which wrapped SimPro's legacy on-prem JSON-RPC API. The rewrite targets
the SimPro Cloud REST API and shares no code with the original; the MIT
licence and lineage are retained with thanks.

## Development

```bash
uv sync
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
uv run mypy
```

## Licence

MIT — see [LICENCE.txt](LICENCE.txt).
