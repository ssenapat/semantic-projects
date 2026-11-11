# pagila-analytics

Ask business questions about the Pagila DVD-rental PostgreSQL database in plain English. The project is a read-only semantic layer plus three services and a Streamlit UI on top of it. Tracked in Jira as GHE-4 ("create pagila database semantic layer").

```
Streamlit UI (browser)
   │  POST /v1/ask
   ▼
llm-intent-parser-service  :8001   question → structured request (Claude, never writes SQL)
   │  GET /v1/catalog, POST /v1/compile, POST /v1/query
   ▼
semantic-query-service     :8000   JSON request → guarded, read-only SQL → results
   │  uses the semantic-layer package (definitions, compiler, SQL guard, executor)
   ▼
PostgreSQL `pagila` (localhost:5432)
```

Only the query service touches the database. Consumers send JSON requests, never SQL.

## Status

| Component | Status |
|---|---|
| Spec: `specs/read_only_semantic_layer_specification.md` | Done |
| `semantic-layer/` (definitions, compiler, SQL guard, executor, CLI, validation SQL) | Done. 89 of 90 validation checks pass, 0 error-level failures. The one failure is informational (the Pagila reference view over-counts revenue because films have several categories). |
| `semantic-query-service/` (HTTP API) | Done |
| `llm-intent-parser-service/` (Claude intent parser) | Done |
| `streamlit-analytics-client/` (UI) | Done |
| `semantic-retrieval-service/` | Built (BM25 + synonyms, port 8002). Not yet used by the intent parser |

## Not done yet

- Business approval of the late-return rule (spec 7.10.1 and 16, item 3). `late_return_count` stays `draft` and is not exposed until approved.
- `semantic-retrieval-service/` is built but the intent parser does not call it yet. Run: `cd semantic-retrieval-service && pip install -e '.[dev]' && python -m semantic_retrieval --port 8002`.
- `docs/glossary.md` (spec 9.4) and `definitions/metrics/derived_metrics.yml` (spec 15) do not exist yet.
- The live tests (database integration tests and the 18 golden questions run against real Claude) are skipped unless credentials are set. They have not been run as part of this README update.
- The project directory is not a git repository.

## Prerequisites

- Python 3.11 or newer.
- Docker (for the local PostgreSQL container), or any PostgreSQL 18 you can reach on `localhost:5432`.
- An Anthropic API key, for the intent parser only.

## 1. Database

The spec expects Pagila loaded into a database named `pagila` on a Docker container called `pg-local` (PostgreSQL 18.6, `pgvector` 0.8.7 installed inside the container).

### Where the data comes from

The data is the **Pagila** sample database, a PostgreSQL port of MySQL's **Sakila** sample database. It models a DVD-rental business (films, actors, categories, inventory, stores, staff, customers, rentals and payments).

- Upstream repository: https://github.com/devrimgunduz/pagila (maintained by Devrim Gündüz)
- Files used: `pagila-schema.sql` (tables, views, functions) and `pagila-data.sql` (the data, loaded with `COPY`). The upstream repo also ships `pagila-insert-data.sql`, which holds the same data as `INSERT` statements.
- Licence: the upstream PostgreSQL licence. Check the upstream repository for the current terms.
- The dump is not stored in this repository (`data/` is only a placeholder). Download it from upstream, or use a local copy in `../pagila/`.

Row counts in the loaded copy, for checking a load: `rental` 51,805 and `payment` 51,061.

If the container is not running, start it:

```
docker start pg-local
```

To build the database from scratch, follow the upstream README at https://github.com/devrimgunduz/pagila. The files are also in `../pagila/`. In outline:

```
docker run -d --name pg-local -p 5432:5432 -e POSTGRES_PASSWORD=<password> postgres:18
createdb -h localhost -U postgres pagila
psql -h localhost -U postgres -d pagila -f ../pagila/pagila-schema.sql
psql -h localhost -U postgres -d pagila -f ../pagila/pagila-data.sql
```

The `pgvector` install is lost if the container is recreated from the plain `postgres` image, so reinstall `postgresql-18-pgvector` after recreating it.

Check the load: `rental` should have 51,805 rows and `payment` 51,061. `python -m semantic_layer validate` (below) checks every exposed table.

## 2. Install (once)

Run from this directory. The repo already has a `.venv`; to create a new one:

```
python3 -m venv .venv
.venv/bin/pip install -e "semantic-layer[dev]" \
                      -e "semantic-query-service[dev]" \
                      -e "llm-intent-parser-service[dev]" \
                      -e "streamlit-analytics-client[dev]"
```

## 3. Run

### Quick start: `run-local.sh` (all services in one command)

`run-local.sh` starts the PostgreSQL container (`pg-local`), the query service (port 8000), the intent parser (port 8001) and the Streamlit UI (port 8511), in that order. It waits for each one to report ready, and it binds only to `127.0.0.1`.

Prerequisites (one-time):

1. The virtualenv exists at `./.venv` with the packages from section 2 installed.
2. `./.env` contains `PAGILA_PG_USER` and `PAGILA_PG_PASSWORD`. The script reads them and never prints them.
3. The `pg-local` Docker container exists (section 1) and Docker Desktop is running, unless you pass `--no-db`.
4. AWS credentials are valid for Bedrock, because the intent parser calls Claude there. Run `aws sso login`, or set `AWS_PROFILE` or the AWS keys.

```bash
./run-local.sh start             # DB + query service + intent parser + UI
./run-local.sh status            # ready / starting / stopped for each piece
./run-local.sh stop              # stop UI, parser and query service (DB keeps running)
./run-local.sh stop --stop-db    # also stop the pg-local container
./run-local.sh restart           # stop, then start
./run-local.sh start --no-db     # skip the Docker step (PostgreSQL already reachable)
```

When `start` finishes it prints `all services ready; UI at http://127.0.0.1:8511`. Open that URL.

| Piece | Port | Readiness URL |
|---|---|---|
| `query` (semantic-query-service) | 8000 | `http://127.0.0.1:8000/ready` |
| `parser` (llm-intent-parser-service) | 8001 | `http://127.0.0.1:8001/health` |
| `ui` (Streamlit) | 8511 | `http://127.0.0.1:8511/_stcore/health` |

Logs and PID files are in `./.run/` (`query.log`, `parser.log`, `ui.log`). If a service does not become ready, the script prints which log to read. If a port is already in use, `start` refuses to launch that service. If any service fails, the script stops the ones it already started.

Environment overrides: `QUERY_PORT`, `PARSER_PORT`, `UI_PORT`, `READY_WAIT_SECONDS` (default 60), `DB_CONTAINER` (default `pg-local`), `AWS_REGION` (default `ap-south-1`) and `INTENT_MODEL` (default `global.anthropic.claude-sonnet-5-5`).

### Manual start (one terminal per service)

The steps below do the same thing by hand.

Use four terminals (or run the first two in the background). Activate the venv in each: `source .venv/bin/activate`.

**Terminal 1: semantic-query-service (port 8000)**

```
export PAGILA_PG_USER=<db user>
export PAGILA_PG_PASSWORD=<db password>
# optional: PAGILA_PG_HOST (localhost), PAGILA_PG_PORT (5432), PAGILA_PG_DATABASE (pagila)
python -m semantic_query_service --port 8000
```

Interactive API docs: http://127.0.0.1:8000/docs. It listens on 127.0.0.1 by default and refuses to listen on any other address unless `SEMANTIC_API_KEYS` is set (`name:key,...`, keys at least 16 characters; callers then send `X-API-Key`).

**Terminal 2: llm-intent-parser-service (port 8001)**

```
aws sso login
export AWS_REGION=ap-south-1
export INTENT_MODEL=global.anthropic.claude-sonnet-5-5   # Bedrock inference profile
export SEMANTIC_QUERY_URL=http://127.0.0.1:8000
# only if the query service uses API keys:
# export SEMANTIC_QUERY_API_KEY=<key>
python -m intent_parser
```

Optional settings: `INTENT_MODEL`, `INTENT_MAX_LLM_CALLS` (default 3), `INTENT_API_KEYS` (required to listen beyond localhost), `INTENT_LOG_QUESTIONS` (off by default, the audit log keeps only a hash).

**Terminal 3: Streamlit UI**

```
export ANALYTICS_INTENT_URL=http://127.0.0.1:8001   # default
# only if the intent parser requires keys:
# export ANALYTICS_INTENT_API_KEY=<key>
streamlit run streamlit-analytics-client/src/analytics_client/app.py
```

Open the URL Streamlit prints (http://localhost:8511; port set in `streamlit-analytics-client/.streamlit/config.toml`, so it does not clash with transport-statistics on 8501). Start the two services first.

### Check that everything is up

```
curl http://127.0.0.1:8000/health    # definitions loaded
curl http://127.0.0.1:8000/ready     # database answers
curl http://127.0.0.1:8001/health
curl http://127.0.0.1:8001/ready
```

### Try it

In the UI, ask for example "What was total revenue by month in 2025?" or "Which film categories rent the most?". Use "Show how answers were produced" to see the structured request, the SQL, the definition versions and the token use. Follow-ups such as "and by store?" build on the last answer; "New conversation" clears the context.

You can also call the query service directly with a JSON request (no Claude key needed):

```
curl -X POST http://127.0.0.1:8000/v1/query -H 'Content-Type: application/json' -d '{
  "model": "payment_activity",
  "measures": ["total_revenue"],
  "dimensions": ["rental_date:month"],
  "filters": [{"dimension": "rental_date", "op": "between", "value": ["2025-01-01", "2025-12-31"]}],
  "limit": 100}'
```

### Semantic layer CLI (no HTTP)

```
python -m semantic_layer catalog                  # what may be asked for
python -m semantic_layer compile request.json     # SQL and caveats, no database access
python -m semantic_layer query request.json       # runs it
python -m semantic_layer validate                 # writes semantic-layer/validation/results/latest.json
```

Only `validated` or `approved` definitions are usable. Add `--allow-draft` to try drafts locally.

## 4. Tests

Each component has its own suite. Run from inside the component directory, for example:

```
cd semantic-layer && ../.venv/bin/pytest -q
```

Last run: `semantic-layer` 48 passed, 11 skipped; `semantic-query-service` 27 passed, 5 skipped; `llm-intent-parser-service` 33 passed, 18 skipped; `streamlit-analytics-client` 37 passed. No failures. The skipped tests need a database, a Claude key or a running service:

- Database integration tests: set `PAGILA_PG_USER` and `PAGILA_PG_PASSWORD`.
- Golden questions against real Claude and the real database: `cd llm-intent-parser-service && ../.venv/bin/pytest -m live` (needs `INTENT_MODEL`, AWS access and both services reachable).

## Troubleshooting

- `/ready` fails or the API returns 503 `database_unavailable`: PostgreSQL is not reachable. Check that `pg-local` is running (`docker ps`) and that the `PAGILA_PG_*` variables are set.
- "Cannot connect to the Docker daemon": start Docker Desktop first.
- The query service refuses to start with "refusing to listen on a non-loopback address": set `SEMANTIC_API_KEYS`, or use `--host 127.0.0.1`.
- 401 `unauthorized`: the `X-API-Key` header is missing or wrong.
- The UI shows a connection error: the intent parser is not running on `ANALYTICS_INTENT_URL`.

## Layout

- `semantic-layer/`: definitions (sources, semantic models, relationships), compiler, SQL guard, executor, CLI and validation SQL.
- `semantic-query-service/`: HTTP API in front of the semantic layer.
- `semantic-retrieval-service/`: no vector search; parser not wired to it.
- `llm-intent-parser-service/`: Claude-based question-to-request parser.
- `streamlit-analytics-client/`: Streamlit chat UI.
- `specs/`: specifications, including the semantic layer spec.
- `data/`: placeholder for local data assets. Nothing is committed.
- `ghe/`: GHE workflow configuration and memory.

## Key modelling decisions

Revenue is dated by `rental_date` (payments do not line up with rentals); category is allocated equally across a film's categories so totals add up; actors are a fan-out and cannot be mixed with category; personal columns are blocked; stores 3 to 500 are test data and hidden.

## Reference

- Upstream Pagila repository: https://github.com/devrimgunduz/pagila
