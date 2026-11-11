# LLM Intent Parser Service (SP-2 C2)

Turns a question plus an LLM-safe semantic context into exactly one outcome: `semantic_request`, `clarification_required` or `unsupported`. It has no catalog, database, network-fetch, shell or tool access, and it never produces SQL. It listens on `127.0.0.1:8081`.

## Run
```bash
pip install -e ".[test]"
# offline / deterministic
PARSER_LLM_CLIENT=stub uvicorn llm_intent_parser_service.app.main:app --host ${PARSER_HOST:-127.0.0.1} --port ${PARSER_PORT:-8081}
# real model: the key comes only from the environment, never from a file
AWS_REGION=... BEDROCK_MODEL_ID=... uvicorn llm_intent_parser_service.app.main:app --host 127.0.0.1 --port 8081
```

## Endpoints
`POST /api/v1/intent/parse` (CONTRACT 4.2), `GET /health/live`, `GET /health/ready` (503 `parser_not_ready` when anthropic mode has no key or model; never calls the LLM), `GET /metrics`, `GET /openapi.json`.
Errors use the envelope of CONTRACT 5.2: 400 `invalid_request`, 409 `catalog_version_mismatch` (optional `X-Catalog-Version` header differs from the context), 422 `invalid_semantic_context`, 422 `invalid_model_output`, 429 `rate_limited`, 502 `llm_provider_failure`, 503 `parser_not_ready`.

## Configuration (env only)
`PARSER_HOST`, `PARSER_PORT`, `PARSER_LLM_CLIENT` (`bedrock` default | `anthropic` | `stub`), `AWS_REGION` and `BEDROCK_MODEL_ID` (Bedrock; AWS credentials from the standard chain, no API key), `ANTHROPIC_API_KEY` (secret, no default), `ANTHROPIC_MODEL` (`claude-sonnet-5-5`), `PARSER_LLM_TIMEOUT_SECONDS` (20), `PARSER_MAX_REPAIR_ATTEMPTS` (1), `PARSER_TEMPERATURE` (0.0, clamped 0.0-0.2), `PARSER_MAX_OUTPUT_TOKENS` (1000), `PARSER_STUB_RESPONSES_FILE`, `PARSER_RATE_LIMIT_PER_MINUTE` (60), `LOG_LEVEL`.

## Validation
Model output is parsed as a discriminated union, then checked against the context: `catalog_version`, every metric in `metrics[]` (1..`max_metrics_per_query`, read from `query_policy.yml`), `combinable_with`, dimensions allowed for every metric, operators and time grains per dimension, date range shape, and SQL/code leakage in free text (CONTRACT 6.2). The clarify trigger rule (REQ-041) rejects a `semantic_request` for a question that matches a clarify trigger. One repair call is made with the errors appended; if the output is still invalid the service returns 422 `invalid_model_output`. It never converts an invalid output into a success. Unknown metrics, metrics that cannot be combined, and a dimension that only some metrics allow are not repaired: the service returns 200 with a deterministic `unsupported` outcome (`confidence` 1.0, `conflicting_metrics` set). The request field is `metrics` (a list); the old `metric` field is rejected.

## Stub client
`StubLlmClient` maps `normalize(question)` to a fixture in `app/services/stub_responses.json` (`{{catalog_version}}` is substituted). It covers the six Spec 48 questions. Any other question yields `unsupported` / `insufficient_context`. Fixture text must itself pass the leakage rule (avoid phrases like "from the").

## Logs and metrics
One JSON line per request on stdout (keys in CONTRACT 6.9). The question text, prompt, context and secrets are never logged; only `question_sha256` and `question_chars`. Metric names are listed in CONTRACT 6.9.

## Tests
`python -m pytest -q` runs fully offline with the stub client. No key or network is needed, and sockets are blocked in tests.
