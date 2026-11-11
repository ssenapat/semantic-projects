import json

import httpx
import pandas as pd
import pytest

from streamlit_analytics_client.components import charts
from streamlit_analytics_client.components.api_client import ApiError, SemanticAnalyticsApiClient
from streamlit_analytics_client.utils import config
from streamlit_analytics_client.utils.errors import user_message
from streamlit_analytics_client.utils.session import add_context, add_history, init_session

from conftest import RESULT, ask_resp


def client(handler):
    return SemanticAnalyticsApiClient("http://x", 5, httpx.MockTransport(handler))


def test_ask_posts_contract_body():
    seen = {}

    def h(req):
        seen["path"], seen["body"] = req.url.path, json.loads(req.content)
        return httpx.Response(200, json=ask_resp(result=RESULT))
    out = client(h).ask("q", ["a"])
    assert seen["path"] == "/api/v1/ask"
    assert seen["body"] == {"question": "q", "conversation_context": ["a"], "authorization_context": {}, "requested_limit": 500}
    assert out["outcome"] == "result"


def test_list_metrics():
    c = client(lambda r: httpx.Response(200, json={"catalog_version": "v", "metrics": []}))
    assert c.list_metrics()["metrics"] == []


def test_error_envelope_to_api_error():
    env = {"type": "https://example.internal/errors/rate-limited", "title": "x", "status": 429, "detail": "slow",
           "request_id": "rid9"}
    with pytest.raises(ApiError) as ei:
        client(lambda r: httpx.Response(429, json=env)).ask("q", [])
    assert (ei.value.status, ei.value.code, ei.value.request_id) == (429, "rate_limited", "rid9")


def test_real_424_envelope_code_from_type():
    env = {"type": "https://example.internal/errors/intent-parser-unavailable", "title": "Intent parser unavailable",
           "status": 424, "detail": "The intent parser is unavailable.", "request_id": "rid424"}
    with pytest.raises(ApiError) as ei:
        client(lambda r: httpx.Response(424, json=env)).ask("q", [])
    assert (ei.value.status, ei.value.code, ei.value.request_id) == (424, "intent_parser_unavailable", "rid424")
    assert ei.value.detail == "The intent parser is unavailable."


def test_transport_error():
    def h(req):
        raise httpx.ConnectError("boom")
    with pytest.raises(ApiError) as ei:
        client(h).ask("q", [])
    assert ei.value.status is None


@pytest.mark.parametrize("status,text", [
    (400, "Please rephrase your question."), (422, "Please rephrase your question."),
    (409, "The metric catalog was updated. Please ask again."),
    (424, "The analytics service is temporarily unavailable. Please try again shortly."),
    (503, "The analytics service is temporarily unavailable. Please try again shortly."),
    (429, "Too many requests. Please wait a moment."),
    (500, "Something went wrong."), (None, "Something went wrong."),
])
def test_messages(status, text):
    assert user_message(status) == text
    assert user_message(status, "abc").endswith("Request ID: abc")


def test_session_caps():
    st = {}
    init_session(st)
    assert set(st) == {"conversation_context", "query_history", "last_response", "pending_clarification", "selected_chart_type"}
    for i in range(15):
        add_context(st, f"q{i}")
    assert st["conversation_context"] == [f"q{i}" for i in range(5, 15)]
    for i in range(60):
        add_history(st, f"q{i}", ask_resp(result=RESULT))
    assert len(st["query_history"]) == 50 and st["query_history"][-1]["question"] == "q59"
    assert set(st["query_history"][0]) == {"question", "outcome", "metrics", "applied_filters", "time_range", "row_count", "request_id", "timestamp"}


def test_config(monkeypatch):
    monkeypatch.delenv("UI_RETRIEVER_BASE_URL", raising=False)
    monkeypatch.delenv("UI_TIMEOUT_SECONDS", raising=False)
    assert config.base_url() == "http://127.0.0.1:8080" and config.timeout_seconds() == 30.0
    monkeypatch.setenv("UI_RETRIEVER_BASE_URL", "http://h:1")
    monkeypatch.setenv("UI_TIMEOUT_SECONDS", "90")
    assert config.base_url() == "http://h:1" and config.timeout_seconds() == 90.0


def test_charts():
    df = charts.to_dataframe(RESULT)
    assert charts.build_chart(RESULT["chart_hints"][0], df) is not None
    assert charts.build_chart(RESULT["chart_hints"][1], df) is None
    big = pd.DataFrame({"c": [str(i) for i in range(30)], "m": range(30)})
    ch = charts.build_chart({"chart_type": "bar", "x_field": "c", "y_fields": ["m"], "confidence": .9}, big)
    assert len(ch.data) == 20
