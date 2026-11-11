import httpx
import pytest

from streamlit_analytics_client.components import api_client

RESULT = {
    "outcome": "result", "request_id": "r1",
    "columns": [{"name": "flight_date__month", "role": "dimension", "label": "Month", "unit": None,
                 "semantic_type": "time_dimension", "data_type": "date"},
                {"name": "flight_count", "role": "metric", "label": "Flight count", "unit": "count",
                 "semantic_type": "metric", "data_type": "integer"}],
    "rows": [["2025-01-01", 5], ["2025-02-01", 7]],
    "truncated": False, "row_count": 2, "metric_versions": {"flight_count": 1},
    "metadata": {"metrics": ["flight_count"], "catalog_version": "sp1_abc", "time_basis": "flight date",
                 "applied_filters": [{"dimension": "carrier", "operator": "equals", "values": ["AA"]}],
                 "generated_at_utc": "2026-01-01T00:00:00Z"},
    "chart_hints": [{"chart_type": "line", "x_field": "flight_date__month", "y_fields": ["flight_count"], "confidence": 0.9},
                    {"chart_type": "table", "confidence": 1.0}],
    "query_execution": {"elapsed_ms": 5, "effective_limit": 500, "reference_date": "2026-01-01",
                        "resolved_date_range": None, "explain_checked": False, "sql_guard": "passed"},
}


def ask_resp(outcome="result", **kw):
    base = {"request_id": "r1", "catalog_version": "sp1_abc", "authorization_mode": "permissive_placeholder",
            "outcome": outcome, "clarification": None, "unsupported": None, "result": None,
            "retrieval_summary": {}}
    base.update(kw)
    return base


@pytest.fixture
def mock_api(monkeypatch):
    """Returns a setter: mock_api(handler) installs a MockTransport for the UI."""
    calls = []

    def install(handler):
        def wrapped(request):
            calls.append(request)
            return handler(request)
        monkeypatch.setattr(api_client, "TRANSPORT_OVERRIDE", httpx.MockTransport(wrapped))
        return calls
    return install
