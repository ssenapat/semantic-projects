# Semantic Query Service (Query, port 8082)

Validates a constrained semantic request, compiles SQL with the SP-1 compiler, guards it, runs it read-only
against PostgreSQL and returns a `QueryResultResponse` with chart hints. The caller is the Retriever only.

## Run (from this folder)
```
export PGPASSWORD=...            # the only password source; never written to a file
uvicorn semantic_query_service.app.main:app --host ${QUERY_HOST:-127.0.0.1} --port ${QUERY_PORT:-8082}
```
Endpoints: `POST /api/v1/query/execute`, `GET /health/live`, `GET /health/ready`, `GET /metrics`, `GET /openapi.json`.

## Configuration (env vars, CONTRACT §2)
`SEMANTIC_LAYER_SRC`, `SEMANTIC_LAYER_DEFINITIONS_DIR`, `SEMANTIC_AUDIENCE`, `LOG_LEVEL`, `QUERY_HOST`, `QUERY_PORT`,
`PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` (secret), `QUERY_REFERENCE_DATE`,
`QUERY_STATEMENT_TIMEOUT_SECONDS`, `QUERY_IDLE_TX_TIMEOUT_SECONDS`, `QUERY_MAX_PAYLOAD_BYTES`,
`QUERY_EXPLAIN_ENABLED`, `QUERY_EXPLAIN_MAX_COST`, `QUERY_ALWAYS_CLARIFY_LOCATIONS`, `QUERY_AUDIT_LOG_PATH`.

## Pipeline
plan (`query_planner`) → `sql_compiler` (SP-1 `compile_request`) → `sql_guard` (sqlglot AST: one SELECT, allowed
sources, no DML/DDL/INTO, prohibited functions, LIMIT required, then SP-1 `assert_read_only_sql`) →
optional `explain_guard` → `PostgresExecutor` (read-only session, statement timeout, single attempt) →
`result_formatter` (columns, rows, chart hints). Every statement, including the reference-date and value-map
lookups, passes the guard. Semantic problems return 200 `unsupported` / `clarification_required`.

Errors use `ErrorEnvelope` (409 `catalog_version_mismatch`, 422 `query_validation_failed` / `sql_guard_rejected`,
500 `query_execution_failed`, 503 `catalog_not_ready` / `database_unavailable`, 504 `query_timeout`).
Logs: one JSON line per request on stdout and a JSONL audit file (`QUERY_AUDIT_LOG_PATH`) with no values, SQL
or secrets. `/metrics` exposes `query_service_*` counters.

## Tests (offline, database mocked)
```
pip install -e .[dev]
python -m pytest tests -q
```
Requires `sqlglot`; psycopg is only needed for a real database.
