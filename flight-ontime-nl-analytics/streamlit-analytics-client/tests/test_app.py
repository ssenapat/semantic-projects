import httpx
from pathlib import Path
from streamlit.testing.v1 import AppTest

from conftest import RESULT, ask_resp

PAGES = Path(__file__).resolve().parents[1] / "streamlit_analytics_client"
ASK = str(PAGES / "pages" / "1_Ask_Analytics.py")


def run_ask(q):
    at = AppTest.from_file(ASK, default_timeout=30).run()
    at.text_input[0].set_value(q)
    at.button[0].click()
    return at.run()


def test_result_rendered(mock_api):
    mock_api(lambda r: httpx.Response(200, json=ask_resp(result=RESULT)))
    at = run_ask("flights per month")
    assert not at.exception
    assert at.session_state["conversation_context"] == ["flights per month"]
    assert at.session_state["last_response"]["outcome"] == "result"
    assert at.session_state["query_history"][0]["metrics"] == ["flight_count"]
    assert at.session_state["selected_chart_type"] == "bar"
    assert at.selectbox[0].options == ["bar", "line", "table"]
    assert any("Request ID: r1" in c.value for c in at.caption)


def test_unsupported(mock_api):
    mock_api(lambda r: httpx.Response(200, json=ask_resp("unsupported", unsupported={
        "message": "Not supported here.", "reason": "no_approved_metric", "suggested_supported_topics": ["delays"]})))
    at = run_ask("weather?")
    assert [w.value for w in at.warning] == ["Not supported here."]


def test_clarification_continuation(mock_api):
    state = {"n": 0}

    def h(req):
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(200, json=ask_resp("clarification_required", clarification={
                "question": "Which airport role?", "ambiguity_type": "airport_or_location",
                "candidate_options": ["origin", "destination"]}))
        return httpx.Response(200, json=ask_resp(result=RESULT))
    calls = mock_api(h)
    at = run_ask("flights at JFK")
    assert at.session_state["pending_clarification"]["question"] == "flights at JFK"
    at.button(key="clar_0").click()
    at.run()
    import json
    body = json.loads([c for c in calls if c.url.path == "/api/v1/ask"][-1].content)
    assert body["question"] == "flights at JFK"
    assert "flights at JFK Selected: origin." in body["conversation_context"]
    assert at.session_state["pending_clarification"] is None
    assert at.session_state["last_response"]["outcome"] == "result"


def test_error_message(mock_api):
    mock_api(lambda r: httpx.Response(503, json={"type": "t", "title": "t", "status": 503, "detail": "d",
                                                  "request_id": "rq7"}))
    at = run_ask("anything")
    assert "temporarily unavailable" in at.error[0].value and "Request ID: rq7" in at.error[0].value


def test_history_and_metrics_pages(mock_api):
    mock_api(lambda r: httpx.Response(200, json={"catalog_version": "v1", "metrics": [{
        "name": "m", "label": "Metric M", "description": "d", "unit": None, "status": "approved", "time_basis": "flight date",
        "allowed_dimensions": [{"name": "carrier", "label": "Carrier"}], "allowed_time_grains": ["month"],
        "synonyms": [], "clarification_notes": [], "caveats": []}]}))
    at = AppTest.from_file(str(PAGES / "pages" / "3_Metric_Definitions.py"), default_timeout=30).run()
    assert not at.exception and at.expander[0].label == "Metric M"
    assert any("Status: approved" in m.value and "Version: v1" in m.value for m in at.markdown)  # TC-143
    at = AppTest.from_file(str(PAGES / "pages" / "2_Query_History.py"), default_timeout=30).run()
    assert not at.exception
    at = AppTest.from_file(str(PAGES / "app.py"), default_timeout=30).run()
    assert not at.exception


# ---- FIX-4 / D5, D6 ------------------------------------------------------------------------------
import json  # noqa: E402

from streamlit_analytics_client.models import session_models as sm  # noqa: E402

CATALOG = {"catalog_version": "sp1_abc", "metrics": [{
    "name": "flight_count", "label": "Flight count", "description": "Number of scheduled flights.", "unit": None,
    "status": "approved", "time_basis": "flight date", "allowed_dimensions": [], "allowed_time_grains": [],
    "synonyms": [], "clarification_notes": [], "caveats": []}]}


def router(ask_json):
    def h(req):
        if req.url.path == "/api/v1/catalog/metrics":
            return httpx.Response(200, json=CATALOG)
        return httpx.Response(200, json=ask_json)
    return h


def with_range(result=RESULT, rows=None):
    r = json.loads(json.dumps(result))
    r["query_execution"]["resolved_date_range"] = {"start_inclusive": "2025-12-01", "end_exclusive": "2026-01-01",
                                                   "time_basis": "flight date"}
    if rows is not None:
        r["rows"] = rows
        r["row_count"] = len(rows)
    return r


def test_header_and_six_chips_submit_on_click(mock_api):  # TC-125, TC-126
    calls = mock_api(router(ask_resp(result=RESULT)))
    at = AppTest.from_file(ASK, default_timeout=30).run()
    assert at.title[0].value == "Airline Analytics Assistant"
    chips = [b for b in at.button if b.key and b.key.startswith("chip_")]
    assert [b.label for b in chips] == list(sm.EXAMPLE_QUESTIONS) and len(chips) == 6
    chips[1].click()
    at.run()
    ask_calls = [c for c in calls if c.url.path == "/api/v1/ask"]
    assert json.loads(ask_calls[-1].content)["question"] == "Show flight delays"
    assert at.session_state["last_response"]["outcome"] == "result"


def test_result_description_version_range_and_expander(mock_api):  # TC-127
    mock_api(router(ask_resp(result=with_range())))
    at = run_ask("flights per month")
    assert not at.exception
    assert any("Number of scheduled flights." in m.value for m in at.markdown)
    caps = " | ".join(c.value for c in at.caption)
    assert "Version: flight_count v1" in caps and "2025-12-01 to 2026-01-01" in caps
    exp = [e for e in at.expander if e.label == "Request details"][0]
    texts = [c.value for c in exp.caption]
    assert "request_id: r1" in texts and "catalog_version: sp1_abc" in texts


def test_zero_rows_no_data_message(mock_api):  # TC-128
    mock_api(router(ask_resp(result=with_range(rows=[]))))
    at = run_ask("flights last month")
    assert not at.exception
    assert any("No data for this period" in i.value and "2025-12-01" in i.value for i in at.info)
    assert len(at.selectbox) == 0


def test_unsupported_topics_are_clickable_and_reask(mock_api):  # TC-135
    topics = ["Flight count", "Delays", "Cancellations", "Fourth topic"]
    calls = mock_api(router(ask_resp("unsupported", unsupported={
        "message": "Not supported.", "reason": "no_approved_metric", "suggested_supported_topics": topics})))
    at = run_ask("weather?")
    btns = [b for b in at.button if b.key and b.key.startswith("topic_")]
    assert [b.label for b in btns] == topics[:3]
    btns[1].click()
    at.run()
    ask_calls = [c for c in calls if c.url.path == "/api/v1/ask"]
    assert json.loads(ask_calls[-1].content)["question"] == "Delays"


def test_history_has_time_range_column(mock_api):  # TC-141
    mock_api(router(ask_resp(result=with_range())))
    at = run_ask("flights per month")
    at = AppTest.from_file(str(PAGES / "pages" / "2_Query_History.py"), default_timeout=30)
    at.session_state["query_history"] = [{
        "question": "q", "outcome": "result", "metrics": ["flight_count", "cancelled_flights"], "applied_filters": [],
        "time_range": "2025-12-01 to 2026-01-01 (end exclusive)", "row_count": 2, "request_id": "r1",
        "timestamp": "2026-01-01T00:00:00Z"}]
    at.run()
    assert not at.exception
    assert "time_range" in list(at.dataframe[0].value.columns)
    assert at.dataframe[0].value["time_range"][0].startswith("2025-12-01")
    assert at.dataframe[0].value["metrics"][0] == "flight_count, cancelled_flights"


def test_chart_title_never_empty():  # TC-170
    from streamlit_analytics_client.components import charts
    df = charts.to_dataframe(RESULT)
    ch = charts.build_chart({**RESULT["chart_hints"][0], "title": None}, df, "Flight count")
    assert ch.title == "Flight count"
    assert charts.build_chart({**RESULT["chart_hints"][0], "title": "Top 20"}, df, "Flight count").title == "Top 20"
    assert charts.build_chart(RESULT["chart_hints"][0], df).title


def test_in_session_counters_and_json_logs(mock_api, capsys):  # TC-147
    mock_api(router(ask_resp(result=RESULT)))
    question = "super secret question text"
    at = run_ask(question)
    assert at.session_state["streamlit_user_questions_total"] == 1
    assert at.session_state["streamlit_chart_render_total"] >= 1
    out = capsys.readouterr().out
    assert question not in out
    recs = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    events = {r["event"]: r for r in recs}
    assert {"user_question", "chart_render"} <= set(events)
    for r in (events["user_question"], events["chart_render"]):
        for k in ("timestamp", "level", "service_name", "service_version", "request_id", "catalog_version",
                  "semantic_version", "outcome", "latency_ms", "event"):
            assert k in r
    assert events["chart_render"]["request_id"] == "r1" and events["chart_render"]["catalog_version"] == "sp1_abc"
