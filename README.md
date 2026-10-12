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

The client ships read-only access to the first-slice resources, one
namespace per resource, hung off the client:

| Namespace | Endpoints |
|---|---|
| `client.companies` | `list(*, search=None, modified_since=None, **filters)`, `get(company_id)` |
| `client.customers` | `list(company_id, ...)`, `get(company_id, customer_id)`, `iter_all(company_id, **filters)` |
| `client.sites` | `list(company_id, ...)`, `get(company_id, site_id)`, `iter_all(company_id, **filters)` |
| `client.jobs` | `list(company_id, ...)`, `get(company_id, job_id)`, `iter_all(company_id, **filters)` |
| `client.leads` | `list(company_id, ...)`, `get(company_id, lead_id)`, `iter_all(company_id, **filters)` — list items are minimal; extra fields come via `columns="ID,LeadName,Stage"` |

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

## Rate limiting

SimPro enforces **10 API calls per second, per build and per IP** — a budget
shared across every integration and UI session hitting the build — and
answers over-limit traffic with `HTTP 429` (strictly enforced since April
2025).

The client keeps you inside that budget:

```python
client = SimproClient(base_url, api_key)  # paced at 9 req/s
client = SimproClient(base_url, api_key, rate_limit=10)  # right at the ceiling
client = SimproClient(base_url, api_key, rate_limit=None)  # you handle pacing
```

- Pacing uses a sliding one-second window (default **9 requests/second**,
  just under the ceiling to leave headroom for other traffic on the shared
  budget) and adds no dependencies.
- The limit is per *build*, not per client: pass one shared limiter to
  several clients on the same tenant so they count against a single budget.

```python
from simpro import SlidingWindowRateLimiter

limiter = SlidingWindowRateLimiter(9)
a = SimproClient(base_url, key_a, rate_limiter=limiter)
b = SimproClient(base_url, key_b, rate_limiter=limiter)
```

- When SimPro still answers `429` (other integrations can consume the
  shared budget), the client retries automatically — up to `max_retries`
  times (default 3), honouring `Retry-After` when present, otherwise
  exponential backoff (1s, doubling, capped at 30s). Set `max_retries=0`
  to disable retrying.

## Errors

All failures raise a subclass of `SimproError`:

| Exception | HTTP status |
|---|---|
| `SimproAuthError` | 401, 403 |
| `SimproNotFound` | 404 |
| `SimproValidationError` | 422 (`.details` holds the raw error list) |
| `SimproRateLimitError` | 429 after retries exhausted (`.retry_after` holds the parsed header, when sent) |
| `SimproError` | anything else, including transport failures |

SimPro 422 bodies are normally `{"errors": [{"path", "message", "value"}, ...]}`
but the `errors` key may be absent — both shapes are handled, and
`SimproValidationError.details` is always a list.

## Deliberate omissions

- **No retry on transport errors** — only `429` is retried automatically
  (see [Rate limiting](#rate-limiting)); connection resets and timeouts
  remain the caller's job.
- **No caching.**
- **No write endpoints** yet — read-only first.
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
