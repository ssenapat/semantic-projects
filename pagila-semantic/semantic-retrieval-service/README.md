# semantic-retrieval-service

Given a business question, returns the measures and dimensions in the semantic catalog that are relevant to it (BM25 over names, labels and descriptions, plus a business-synonym map). It reads the catalog from the semantic-query-service and has no database access.

## Run
```bash
pip install -e '.[dev]'
export SEMANTIC_QUERY_URL=http://127.0.0.1:8000   # SEMANTIC_QUERY_API_KEY if the query service needs one
python -m semantic_retrieval --port 8002
```
Optional: `RETRIEVAL_API_KEYS=key1,key2` to require an `X-API-Key` header.

## API
- `GET /health`, `GET /ready` (503 if the catalog is unreachable)
- `POST /v1/retrieve` with `{"question": "...", "limit": 10}` returns `models`, ranked `matches` (measures carry `combinable_with` and `allowed_dimensions`), `ambiguous_terms` and the catalog subset. When `limit` is omitted it is `max_measures_per_query + max_dimensions_per_query` from the query service policy (`GET /v1/context`). 422 `no_retrievable_context` if nothing matches.

## Test
`pytest -q`

Ambiguous words (store, category, ...) are listed under `ambiguous` in the same file; every candidate dimension is returned in `matches` and in `ambiguous_terms`. Synonyms live in `src/semantic_retrieval/glossary.json` (override the file with `RETRIEVAL_GLOSSARY`).

Not yet built: embedding/vector search, and wiring the intent parser to use this service.
