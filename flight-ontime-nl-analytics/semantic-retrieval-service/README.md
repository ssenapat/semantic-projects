# Semantic Retrieval Service (Retriever)

Port 8080 (127.0.0.1). It retrieves LLM-safe semantic context from the SP-1 catalog, calls the Intent Parser (8081)
and then the Query Service (8082), and returns one `AskResponse`. It never opens a database connection.

## Run

```
pip install -e ".[dev]"
uvicorn semantic_retrieval_service.app.main:app --host $RETRIEVER_HOST --port $RETRIEVER_PORT
```

## Endpoints
`POST /api/v1/ask`, `POST /api/v1/retrieve`, `GET /api/v1/catalog/status`, `GET /api/v1/catalog/metrics`,
`GET /health/live`, `GET /health/ready` (503 `catalog_not_ready` when the catalog is not loaded), `GET /metrics`, `GET /openapi.json`.
Errors use the `ErrorEnvelope` (`type,title,status,detail,request_id`) with codes
`invalid_request` 400, `catalog_version_mismatch` 409, `no_retrievable_context` 422,
`intent_parser_unavailable` / `query_service_unavailable` 424, `rate_limited` 429, `catalog_not_ready` 503.

## Configuration (env vars)
`SEMANTIC_LAYER_SRC`, `SEMANTIC_LAYER_DEFINITIONS_DIR`, `SEMANTIC_AUDIENCE`, `LOG_LEVEL`, `RETRIEVER_HOST`, `RETRIEVER_PORT`,
`PARSER_BASE_URL`, `QUERY_SERVICE_BASE_URL`, `RETRIEVER_PARSER_TIMEOUT_SECONDS`, `RETRIEVER_QUERY_TIMEOUT_SECONDS`,
`RETRIEVER_RATE_LIMIT_PER_MINUTE`, `RETRIEVER_MAX_CONTEXT_TOKENS`, `RETRIEVER_VECTOR_ENABLED`,
`RETRIEVER_EMBEDDING_PROVIDER`, `RETRIEVER_EMBEDDING_API_KEY` (secret, only read when vector is enabled), `RETRIEVER_ARTIFACTS_DIR`.
No secrets are stored in files.

## Logs and metrics
One JSON log line per request with `request_id`, `catalog_version`, `outcome`, `latency_ms`. It never contains the question text
(only a SHA-256 and the length), the prompt, context, SQL or secrets. `/metrics` serves `semantic_retriever_*` text counters.

## Tests
`python -m pytest tests/api -q` runs offline with stub Parser/Query clients.
