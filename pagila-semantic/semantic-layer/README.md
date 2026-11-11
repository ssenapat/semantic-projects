# semantic-layer

Read-only semantic layer for the Pagila PostgreSQL database. Implements `specs/read_only_semantic_layer_specification.md`.

Requests are JSON (never SQL). The compiler turns them into parameterised `SELECT`s using only the approved definitions in `definitions/`; a SQL guard checks the result; the executor runs it in a read-only session with a statement timeout.

## Setup
```
python3 -m venv .venv && .venv/bin/pip install -e semantic-layer[dev]
export PAGILA_PG_USER=... PAGILA_PG_PASSWORD=...   # PAGILA_PG_HOST/PORT/DATABASE default to localhost/5432/pagila
```

## Use
```
python -m semantic_layer catalog
python -m semantic_layer compile request.json     # SQL + params + caveats, no database access
python -m semantic_layer query request.json       # runs it
python -m semantic_layer validate                 # validation + reconciliation + probes, writes validation/results/latest.json
```
Only `validated`/`approved` definitions are usable. Use `--allow-draft` to try drafts locally.

## Requests (multi-measure, multi-dimension)
```json
{"model": "payment_activity", "measures": ["total_revenue", "payment_count"], "dimensions": ["rental_date:month"],
 "order_by": [{"field": "rental_date"}], "limit": 100}
```
- Limits come from `definitions/policy/query_policy.yml` (measures, dimensions, default and maximum rows, joins, fan-out); nothing is hard-coded.
- One `SELECT` with one `GROUP BY`. Default order is the first measure descending, then dimensions ascending, `NULLS LAST` on every key. SQL ends with `LIMIT <effective + 1>`; `truncated` says more rows existed.
- Rule order (first failing rule wins, nothing executes): unknown fields (legacy `measure` is one), measure count, duplicates, unknown or unapproved measures, measures from different models (`mixed_models`), different `combinable_with` tags (`not_combinable`), dimension count and duplicates, `allowed_dimensions` (`dimension_not_allowed`), time grain (`grain_not_allowed`), fan-out and allocation, `order_by`.
- `query` returns `columns` (name, role, label, unit, type), `rows`, `row_count`, `truncated`, `measure_versions`, `caveats` and `caveat_codes`.
- Every `query` writes one JSON audit line on logger `semantic_layer.audit` (caller, request, measures, dimensions, per-measure versions, SQL, row count, duration, error). Rows and parameters are never logged, and every filter value in the logged `request` is replaced by `"<redacted>"` (dimension and op are kept).

## Layout
- `definitions/` sources, semantic models, relationships (YAML, versioned, with a change log)
- `validation/` one `.sql` file per check; every definition names the checks that prove it
- `src/semantic_layer/` loader, compiler, guard, executor, service, CLI
- `tests/` unit tests need no database; `test_integration.py` runs when credentials are set

## Decisions that shape the numbers
Revenue is dated by `rental_date` (payments don't line up with rentals); category is allocated equally across a film's categories; actors are fan-out and cannot be mixed with category; personal columns are blocked; `late_return_count` is draft and not exposed until the business approves the rule.
