# Streamlit analytics client (SP-2, S6)

Calls only the Retriever (`POST /api/v1/ask`, `GET /api/v1/catalog/metrics`) through `SemanticAnalyticsApiClient`.

Run: `streamlit run streamlit_analytics_client/app.py --server.address 127.0.0.1 --server.port $UI_PORT`

Env: `UI_RETRIEVER_BASE_URL` (default `http://127.0.0.1:8080`), `UI_TIMEOUT_SECONDS` (default `30`), `UI_CSV_DOWNLOAD_ENABLED` (default `false`), `UI_PORT` (default `8501`). No secrets are used or shown.

Tests (offline, httpx MockTransport + Streamlit AppTest): `python -m pytest -q`
