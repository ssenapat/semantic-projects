# streamlit-analytics-client

A chat-style Streamlit page for asking business questions about the Pagila database. It talks only to `llm-intent-parser-service` (`POST /v1/ask`); it has no database access and no model key.

## Run
    pip install -e '.[dev]'
    export ANALYTICS_INTENT_URL=http://127.0.0.1:8001        # default
    export ANALYTICS_INTENT_API_KEY=...                      # only if the service requires keys
    streamlit run src/analytics_client/app.py

Start `semantic-query-service` (port 8000) and `llm-intent-parser-service` (port 8001) first.

## What it shows
- Every answer states the assumptions made (for example "revenue is dated by the rental date") and the caveats (partial last month, unpaid rentals, allocated categories).
- A metric, line chart, bar chart or table, chosen from the shape of the request, plus a CSV download.
- Clarifying questions as buttons, and "not supported" with the reason.
- "Show how answers were produced" reveals the structured request, the SQL, the definition versions and the token use.
- Follow-ups such as "and by store?" build on the last answered question; "New conversation" clears the context.

## Safety
Text that comes from the model or the data is escaped before display, so it cannot render links, images or HTML. CSV cells that look like spreadsheet formulas are defanged. Errors show a plain message, never a stack trace.

## Configuration
`ANALYTICS_INTENT_URL`, `ANALYTICS_INTENT_API_KEY`, `ANALYTICS_REQUEST_TIMEOUT` (seconds, default 120).

## Tests
`pytest -q` runs without any service: the helpers are tested directly, and the page is driven with Streamlit's `AppTest` against a fake API.

## Display

Tables show one column per dimension and per measure, headed by the definition label, with values formatted by unit (currency `1,234.50`, count `1,234`, days `5.03`). Each answer shows `Model: <model>`, and a truncated answer shows `Only the first <row_count> rows are shown.` Line charts are sorted by x; at most 3 charts are drawn.
