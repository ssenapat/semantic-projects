# semantic-query-service

HTTP API in front of `semantic-layer`. Consumers (the intent parser, Streamlit, anything else) send a **semantic request** as JSON and get back data, the SQL that ran, the definition versions used and every caveat. They never send SQL: a body with an unknown field such as `sql` is refused.

## Run
```
.venv/bin/pip install -e semantic-layer -e semantic-query-service[dev]
export PAGILA_PG_USER=... PAGILA_PG_PASSWORD=...
python -m semantic_query_service --port 8000       # interactive docs at /docs
```

## Endpoints
| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness: definitions loaded (no auth) |
| GET | `/ready` | readiness: the database answers (no auth) |
| GET | `/v1/catalog` | models, measures (with `combinable_with` and `allowed_dimensions`) and dimensions that may be asked for |
| GET | `/v1/context` | the last date that has data (for resolving "last month"), model versions and the query `policy` limits |
| POST | `/v1/compile` | validate a request and show the SQL; does not touch the database |
| POST | `/v1/query` | run a request |
| GET | `/v1/validation` | latest validation and reconciliation report |

Request (spec section 10; contract 2.4). `measures` is a list (1 to `policy.max_measures_per_query`), `dimensions` at most `policy.max_dimensions_per_query`; both limits are read from the policy, never hard-coded. A legacy singular `measure` field is refused with 422 `invalid_request`. All measures must belong to the one `model`.
```json
{"model": "payment_activity", "measures": ["total_revenue", "payment_count"], "dimensions": ["rental_date:month"],
 "filters": [{"dimension": "rental_date", "op": "between", "value": ["2025-01-01", "2025-12-31"]}],
 "order_by": [{"field": "total_revenue", "direction": "desc"}], "limit": 100}
```
Optional: `timezone`, `accept_fan_out`. The `/v1/query` response carries `columns` (dimensions in request order, then measures), `rows`, `row_count`, `truncated`, `sql`, `params`, `definitions_used`, `measure_versions`, `caveats`, `caveat_codes` and `duration_ms`. `/v1/compile` returns the same `columns`, `measure_versions` and `caveat_codes` without touching the database.

## Errors
Always `{"error": {"type", "message", "request_id"}}`; `unsupported` errors add `code` and `details`. The request id is also in the `X-Request-ID` header, and a caller-supplied one is kept.

| Status | type | Meaning |
|---|---|---|
| 401 | `unauthorized` | missing or wrong `X-API-Key` |
| 411 / 413 | `length_required` / `payload_too_large` | body must have a length and be at most 64 KiB |
| 422 | `invalid_request` | wrong shape or types, or unknown fields (including `sql`) |
| 422 | `request_not_allowed` | well formed but refused: unknown or draft measure, personal data, fan-out clash |
| 422 | `unsupported` | well formed but the layer cannot answer it as asked; `code` is `mixed_models` (details `measure_models`), `not_combinable` (`combinable_with`), `dimension_not_allowed` or `grain_not_allowed` (`dimension`, `blocking_measures`) |
| 503 | `busy` | too many queries running (`Retry-After: 2`) |
| 503 | `database_unavailable` | database unreachable (`Retry-After: 5`); no connection details are returned |
| 504 | `query_timeout` | exceeded the 30 s statement timeout |
| 500 | `internal_error` | a bug; the detail is only in the server log |

## Configuration (environment)
| Variable | Default | |
|---|---|---|
| `PAGILA_PG_USER`, `PAGILA_PG_PASSWORD` | required | database login |
| `PAGILA_PG_HOST`, `_PORT`, `_DATABASE`, `_STATEMENT_TIMEOUT_MS` | localhost, 5432, pagila, 30000 | |
| `SEMANTIC_API_KEYS` | unset | `name:key,name2:key2`, keys of at least 16 characters. When set, `/v1/*` needs `X-API-Key`, and the key's name is the caller in the audit log |
| `SEMANTIC_MAX_CONCURRENT_QUERIES` | 8 | queries allowed to run at once; the rest wait `SEMANTIC_QUEUE_WAIT_SECONDS` (2) then get 503 |
| `SEMANTIC_MAX_BODY_BYTES` | 65536 | |
| `SEMANTIC_CORS_ORIGINS` | none | comma-separated origins; CORS is off unless set |
| `SEMANTIC_ALLOW_DRAFT` | false | expose draft definitions (local experiments only) |

The service listens on `127.0.0.1` by default and refuses to start on any other address unless `SEMANTIC_API_KEYS` is set.

## Not in this service
Per-caller rate limits, TLS and key rotation belong in whatever sits in front of it. The database login is still the `postgres` superuser locally; create a read-only role before sharing it (spec section 4.3).
