"""Multi-metric client behaviour: C-2.5 parsing, formatting, chart selection, truncation, definitions (REQ-036..041)."""
import copy
import json

import httpx
import pytest
from streamlit.testing.v1 import AppTest

from conftest import RESULT, ask_resp
from streamlit_analytics_client.components import charts, formatting
from streamlit_analytics_client.components.api_client import ApiError, result_of
from streamlit_analytics_client.models import session_models as sm
from streamlit_analytics_client.utils.session import add_history, init_session

from test_app import ASK, PAGES, run_ask


def col(name, role, unit=None, label=None):
    return {"name": name, "role": role, "label": label or name.title(), "unit": unit,
            "semantic_type": "metric" if role == "metric" else "dimension", "data_type": "x"}


def shape(*cols):
    return {"columns": list(cols), "rows": []}


D1, D2, D3 = col("a", "dimension"), col("b", "dimension"), col("c", "dimension")
CNT, CNT2, RAT = col("n", "metric", "count"), col("n2", "metric", "count"), col("r", "metric", "ratio")


def multi_result(truncated=False):
    r = copy.deepcopy(RESULT)
    r["columns"].append(col("cancelled_flights", "metric", "count", "Cancelled flights"))
    r["rows"] = [["2025-01-01", 12400, 31], ["2025-02-01", 7, None]]
    r["row_count"] = 2
    r["truncated"] = truncated
    r["metric_versions"] = {"flight_count": 1, "cancelled_flights": 3}
    r["metadata"]["metrics"] = ["flight_count", "cancelled_flights"]
    r["chart_hints"] = [{"chart_type": "table", "confidence": 1.0}]
    return r


# ---- formatting (C-2.9.3 / C-2.9.4) ----
@pytest.mark.parametrize("v,u,out", [
    (12400, "count", "12,400"), (12399.6, "count", "12,400"), (0.0312, "ratio", "3.12%"),
    (4.1, "percent", "4.10%"), (None, "count", ""), ("AA", None, "AA"), (5, "other", "5")])
def test_format_value(v, u, out):
    assert formatting.format_value(v, u) == out


def test_formatted_table_one_column_per_metric():
    df = formatting.formatted_table(multi_result())
    assert list(df.columns) == ["Month", "Flight count", "Cancelled flights"]
    assert df.iloc[0].tolist() == ["2025-01-01", "12,400", "31"]
    assert df.iloc[1].tolist() == ["2025-02-01", "7", ""]


# ---- truncation (C-2.9.5) ----
def test_truncation_notice_text():
    assert formatting.truncation_notice(multi_result()) is None
    assert formatting.truncation_notice(multi_result(True)) == "Results were truncated: showing the first 2 rows."
    assert formatting.truncation_notice({"truncated": False, "row_count": 1}) is None


def test_truncation_warning_rendered(mock_api):
    mock_api(lambda r: httpx.Response(200, json=ask_resp(result=multi_result(True))))
    at = run_ask("q")
    assert not at.exception
    assert "Results were truncated: showing the first 2 rows." in [w.value for w in at.warning]


def test_no_warning_when_not_truncated(mock_api):
    mock_api(lambda r: httpx.Response(200, json=ask_resp(result=multi_result())))
    assert not run_ask("q").warning


# ---- chart selection (C-2.9.1) ----
@pytest.mark.parametrize("cols,view", [
    ((D1, CNT), "bar"), ((D1, CNT, CNT2), "grouped_bar"), ((D1, D2, CNT), "pivot"), ((D1, D2, CNT, CNT2), "pivot"),
    ((D1, D2, D3, CNT), "table"), ((D1, CNT, RAT), "table"), ((CNT,), "kpi"), ((CNT, CNT2), "table"),
    ((D1, RAT, col("p", "metric", "percent")), "grouped_bar"), ((D1, D2, CNT, RAT), "table"), ((), "table")])
def test_select_view(cols, view):
    assert charts.select_view(shape(*cols)) == view


def test_grouped_bar_and_pivot_frames():
    r = multi_result()
    df = charts.to_dataframe(r)
    chart = charts.build_grouped_bar(r, df, "t")
    assert chart is not None and set(chart.data["metric"]) == {"Flight count", "Cancelled flights"}
    r2 = {"columns": [D1, D2, CNT], "rows": [["x", "p", 1], ["x", "q", 2], ["y", "p", 3]]}
    pv = charts.pivot_frame(r2, charts.to_dataframe(r2))
    assert pv.shape == (2, 2)


def test_view_options_default_first(mock_api):
    mock_api(lambda r: httpx.Response(200, json=ask_resp(result=multi_result())))
    at = run_ask("q")
    assert at.selectbox[0].options == ["grouped_bar", "table"] and at.selectbox[0].value == "grouped_bar"
    assert at.subheader[0].value == "Flight count, Cancelled flights"
    caps = " | ".join(c.value for c in at.caption)
    assert "Version: flight_count v1, cancelled_flights v3" in caps and "Rows: 2" in caps


# ---- tolerant parsing (C-2.9.8 / C-2.9.9, REQ-036) ----
def test_result_of_parses_and_ignores_extras():
    r = multi_result()
    r["surprise"] = 1
    q = result_of(ask_resp(result=r))
    assert q.row_count == 2 and q.columns[1].role == "metric" and q.metric_versions["cancelled_flights"] == 3
    assert result_of({"outcome": "result", **r}).truncated is False


def test_result_of_none_for_other_outcomes():
    assert result_of(ask_resp("unsupported")) is None


@pytest.mark.parametrize("field", ["columns", "rows", "truncated", "row_count", "metric_versions"])
def test_result_of_missing_field_raises(field):
    r = multi_result()
    del r[field]
    with pytest.raises(ApiError) as ei:
        result_of(ask_resp(result=r))
    assert ei.value.detail == "invalid response" and ei.value.request_id == "r1" and ei.value.status is None


def test_result_of_invalid_values_raise():
    for patch in ({"truncated": "yes"}, {"row_count": "2"}, {"metric_versions": {"a": "x"}}):
        r = {**multi_result(), **patch}
        with pytest.raises(ApiError):
            result_of(ask_resp(result=r))
    r = multi_result()
    r["columns"][0]["role"] = "bogus"
    with pytest.raises(ApiError):
        result_of(ask_resp(result=r))


def test_invalid_result_shows_error_not_crash(mock_api):
    bad = multi_result()
    del bad["truncated"]
    mock_api(lambda r: httpx.Response(200, json=ask_resp(result=bad)))
    at = run_ask("q")
    assert not at.exception and "Request ID: r1" in at.error[0].value


# ---- history (C-2.9.7) ----
def test_history_stores_metrics_list():
    st = {}
    init_session(st)
    add_history(st, "q", ask_resp(result=multi_result()))
    add_history(st, "q2", ask_resp("unsupported"))
    h = st["query_history"]
    assert h[0]["metrics"] == ["flight_count", "cancelled_flights"] and h[0]["row_count"] == 2
    assert h[1]["metrics"] == []
    assert sm.HISTORY_FIELDS == ("question", "outcome", "metrics", "applied_filters", "time_range", "row_count",
                                 "request_id", "timestamp")


# ---- metric definitions (C-2.9.10) ----
def test_definitions_page_combines_with(mock_api):
    def m(name, label, grp):
        return {"name": name, "label": label, "description": "d", "unit": "count", "status": "approved",
                "time_basis": "fl_date", "combinable_with": grp, "allowed_dimensions": [{"name": "carrier", "label": "Carrier"}],
                "allowed_time_grains": [], "synonyms": [], "clarification_notes": [], "caveats": []}
    cat = {"catalog_version": "v", "metrics": [m("b_m", "B metric", "g"), m("a_m", "A metric", "g"),
                                              m("c_m", "C metric", "g"), m("solo", "Solo", None)]}
    mock_api(lambda r: httpx.Response(200, json=cat))
    at = AppTest.from_file(str(PAGES / "pages" / "3_Metric_Definitions.py"), default_timeout=30).run()
    assert not at.exception
    texts = [x.value for x in at.markdown]
    assert "Combines with: A metric, C metric" in texts  # for b_m
    assert "Combines with: B metric, C metric" in texts  # for a_m
    assert texts.count("Combines with: none") == 1
    assert any("Dimensions: Carrier" in t for t in texts)
