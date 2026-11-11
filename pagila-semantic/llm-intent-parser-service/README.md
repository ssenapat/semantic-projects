# llm-intent-parser-service

Turns a business question in plain English into a structured semantic request, using Claude on AWS Bedrock (or the Anthropic API). It never writes SQL.

## How it works
1. Fetch the catalog from `semantic-query-service` (cached 5 minutes) and put it in the system prompt.
2. Claude answers by calling exactly one tool: `submit_request`, `ask_clarification` or `not_supported`. The `submit_request` schema has no SQL field.
3. A submitted request is checked by the real compiler through `/v1/compile`. If it is refused, the error goes back to Claude, which repairs it (at most `INTENT_MAX_LLM_CALLS`, default 3).
4. `/v1/ask` runs a ready request through `/v1/query`. Only the query service touches the database.

Everything the model may use comes from the catalog, so draft definitions (late returns) and personal columns cannot be reached.

## Run
    pip install -e '.[dev]'
    aws sso login                      # credentials come from the normal AWS chain; no API key
    export AWS_REGION=ap-south-1       # or set it in your AWS profile
    export INTENT_MODEL=global.anthropic.claude-sonnet-5-5   # a Bedrock model or inference profile id
    export SEMANTIC_QUERY_URL=http://127.0.0.1:8000
    python -m intent_parser            # 127.0.0.1:8001

Endpoints: `GET /health`, `GET /ready`, `POST /v1/parse` (question to request), `POST /v1/ask` (parse and run).
Body: `{"question": "...", "previous_request": {...}}` (the second field is optional, for follow-ups).
Status is `ready`, `needs_clarification`, `not_supported` or `failed`.

## Configuration
`INTENT_MODEL` (required; Bedrock model or inference profile id), `INTENT_LLM_PROVIDER` (`bedrock` by default, or `anthropic`, which needs `ANTHROPIC_API_KEY`), `AWS_REGION` and `AWS_PROFILE` (standard AWS variables), `INTENT_API_KEYS` (`name:key,...`, required to listen beyond localhost), `INTENT_MAX_TOKENS`, `INTENT_MAX_LLM_CALLS`, `INTENT_MAX_QUESTION_CHARS`, `INTENT_MAX_CONCURRENT`, `INTENT_LLM_TIMEOUT_SECONDS`, `SEMANTIC_QUERY_API_KEY`, `INTENT_LOG_QUESTIONS` (off by default: the audit log keeps only a hash).

## Tests
`pytest -q` runs 63 tests with a scripted model (no key needed). `pytest -m live` runs 25 golden questions in `evals/golden.json` against real Claude (needs `INTENT_MODEL`, AWS access and `PAGILA_PG_*`) and the real database.

## Multi-measure requests and the query policy

The `submit_request` tool limits are not literals: `measures.maxItems`, `dimensions.maxItems` and `limit.maximum` come from the `policy` block of `GET /v1/context` (`max_measures_per_query`, `max_dimensions_per_query`, `max_rows`), and the same numbers are rendered under `LIMITS` in the system prompt. If `/v1/context` carries no usable `policy`, the parser fails closed with `UpstreamError` and never calls the model.

Several measures and dimensions of one model can be combined. Measures from different models are refused by the query service with type `unsupported`; the parser then answers `not_supported` with the refusal message as `reason` and makes no repair call. An ambiguous term such as "store" is resolved inside the model fixed by the requested measures; `needs_clarification` is used only when no measure fixes the model ("Activity by store"). `evals/golden.json` compares measures and dimensions as sets.
