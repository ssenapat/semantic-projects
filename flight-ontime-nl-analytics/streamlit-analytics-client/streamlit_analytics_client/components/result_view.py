from __future__ import annotations

import streamlit as st

from ..models import session_models as sm
from ..utils import config, observability
from ..utils.errors import user_message
from ..utils.session import format_range
from . import charts, formatting
from .api_client import ApiError, make_client, result_of


def _queue_question(question: str) -> None:
    st.session_state[sm.KEY_QUEUED_QUESTION] = question


def render_response(resp: dict) -> None:
    outcome = resp.get("outcome")
    if outcome == "result" and resp.get("result"):
        try:
            result_of(resp)
        except ApiError as e:
            st.error(user_message(e.status, e.request_id or resp.get("request_id")))
            return
        _render_result(resp)
    elif outcome == "unsupported" and resp.get("unsupported"):
        u = resp["unsupported"]
        st.warning(u.get("message", "This question is not supported."))
        topics = (u.get("suggested_supported_topics") or [])[:3]
        if topics:
            st.write("You could ask about:")
            for i, topic in enumerate(topics):
                st.button(str(topic), key=f"topic_{i}", on_click=_queue_question, args=(str(topic),))
    elif outcome == "clarification_required" and resp.get("clarification"):
        st.info(resp["clarification"].get("question", ""))
    else:
        st.error("Something went wrong.")
    if resp.get("request_id"):
        st.caption(f"Request ID: {resp['request_id']}")


def _metric_description(name) -> str:
    """Definition text from the user-safe catalog (cached per session)."""
    cache = st.session_state.setdefault(sm.KEY_METRIC_DESCRIPTIONS, {})
    if name and name not in cache:
        try:
            metrics = make_client().list_metrics().get("metrics") or []
            for m in metrics:
                if isinstance(m, dict) and m.get("name"):
                    cache.setdefault(m["name"], m.get("description", ""))
        except ApiError:
            pass
        cache.setdefault(name, "")
    return cache.get(name, "")


def _render_result(resp: dict) -> None:
    result = resp["result"]
    meta = result.get("metadata", {})
    columns = result.get("columns", [])
    dims = [c for c in columns if c.get("role") == "dimension"]
    mets = [c for c in columns if c.get("role") == "metric"]
    label = ", ".join(c.get("label") or c["name"] for c in mets) or "Result"
    st.subheader(label)
    metric_names = meta.get("metrics") or [c["name"] for c in mets]
    description = _metric_description(metric_names[0]) if len(metric_names) == 1 else ""
    if description:
        st.write(description)
    exe = result.get("query_execution") or {}
    time_range = format_range(exe.get("resolved_date_range"))
    cap = []
    versions = result.get("metric_versions") or {}
    if versions:
        cap.append("Version: " + ", ".join(f"{m} v{v}" for m, v in versions.items()))
    if meta.get("time_basis"):
        cap.append(f"Time basis: {meta['time_basis']}")
    if time_range:
        cap.append(f"Date range: {time_range}")
    cap.append(f"Rows: {result.get('row_count', 0)}")
    st.caption(" · ".join(cap))
    if meta.get("applied_filters"):
        st.write("Filters: " + "; ".join(
            f"{f.get('dimension')} {f.get('operator')} {', '.join(map(str, f.get('values', [])))}"
            for f in meta["applied_filters"]))
    notice = formatting.truncation_notice(result)
    if notice is not None:
        st.warning(notice)
    with st.expander("Request details"):
        st.caption(f"request_id: {resp.get('request_id', '')}")
        st.caption(f"catalog_version: {meta.get('catalog_version') or resp.get('catalog_version', '')}")
    df = charts.to_dataframe(result)
    if df.empty or not result.get("rows"):
        st.info("No data for this period" + (f": {time_range}" if time_range else "."))
        return
    hints = result.get("chart_hints") or [{"chart_type": "table", "confidence": 1.0}]
    default = charts.select_view(result)
    types = list(dict.fromkeys([default] + charts.hint_types(hints) + ["table"]))
    state = st.session_state
    if state.get(sm.KEY_CHART) not in types:
        state[sm.KEY_CHART] = default
    chosen = st.selectbox("View as", types, index=types.index(state[sm.KEY_CHART]), key="chart_choice")
    state[sm.KEY_CHART] = chosen
    hint = next((h for h in hints if h.get("chart_type") == chosen), None)
    if hint is None and chosen == "bar" and dims and mets:
        hint = {"chart_type": "bar", "x_field": dims[0]["name"], "y_fields": [mets[0]["name"]]}
    if chosen == "kpi":
        m = mets[0]
        st.metric(m.get("label") or m["name"], formatting.format_value(df.iloc[0][m["name"]], m.get("unit")))
        observability.count_chart_render(state, resp)
    elif chosen == "pivot" and len(dims) == 2 and mets:
        st.dataframe(charts.pivot_frame(result, df), use_container_width=True)
        observability.count_chart_render(state, resp)
    elif chosen == "grouped_bar":
        chart = charts.build_grouped_bar(result, df, label)
        if chart is not None:
            st.altair_chart(chart, use_container_width=True)
            observability.count_chart_render(state, resp)
        else:
            st.dataframe(formatting.formatted_table(result), use_container_width=True)
    elif chosen == "table" or hint is None:
        st.dataframe(formatting.formatted_table(result), use_container_width=True)
    else:
        chart = charts.build_chart(hint, df, label)
        if chart is not None:
            st.altair_chart(chart, use_container_width=True)
            observability.count_chart_render(state, resp)
        else:
            st.dataframe(formatting.formatted_table(result), use_container_width=True)
    if chosen != "table":
        with st.expander("Data"):
            st.dataframe(formatting.formatted_table(result), use_container_width=True)
    if config.csv_download_enabled():
        st.download_button("Download CSV", df.to_csv(index=False), file_name="result.csv", mime="text/csv")
