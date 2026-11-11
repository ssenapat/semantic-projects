"""Streamlit app. Run:  streamlit run src/analytics_client/app.py"""
from __future__ import annotations

import re

import streamlit as st

from analytics_client import view
from analytics_client.api import ApiError, IntentApi
from analytics_client.view import plain

MAX_TURNS = 20
EXAMPLES = [
    "How much revenue did we make each month?",
    "Top 10 films by number of rentals",
    "Revenue by store",
    "Rentals by category",
    "Average rental duration by rating",
    "How many rentals are still open at each store?",
]


def get_api() -> IntentApi:
    return IntentApi()


def render_answer(turn_id: int, result: dict) -> None:
    request, answer = result["request"], result["answer"]
    df = view.answer_frame(answer, request)

    if answer.get("model"):
        model = str(answer["model"])
        st.caption(f"Model: {model if re.fullmatch(r'[A-Za-z0-9_.-]+', model) else plain(model)}")

    for line in result.get("assumptions") or []:
        st.info(plain(line), icon=":material/lightbulb:")

    if df.empty:
        st.warning("The query ran but returned no rows.")
    elif len(df) == 1 and len(df.columns) == len(request.get("measures") or []):
        cols = st.columns(len(df.columns))
        for col, name in zip(cols, df.columns):
            value = df[name].iloc[0]
            meta = view.column_meta(answer).get(name, {})
            col.metric((meta.get("label") if meta.get("label", name) != name else name.replace("_", " ")), view.format_value(value, meta.get("unit")) if meta.get("unit") else (f"{value:,.2f}" if isinstance(value, float) else f"{value:,}" if isinstance(value, int) else str(value)))
    else:
        for plan in view.chart_plans(request, df):
            args = {k: plan[k] for k in ("x", "y", "color") if k in plan}
            (st.line_chart if plan["kind"] == "line" else st.bar_chart)(view.chart_frame(df, plan), **args)
        st.dataframe(view.display_frame(df, view.column_meta(answer)), hide_index=True, width="stretch", key=f"table-{turn_id}")
        st.download_button("Download CSV", view.csv_bytes(df), file_name="answer.csv", mime="text/csv", key=f"csv-{turn_id}")

    for caveat in answer.get("caveats") or []:
        st.caption(":material/warning: " + plain(caveat))
    if answer.get("truncated"):
        st.warning(f"Only the first {answer['row_count']} rows are shown.")

    if st.session_state.get("show_details"):
        with st.expander("How this was answered"):
            st.markdown("**Request**")
            st.json(request, expanded=False)
            st.markdown("**SQL**")
            st.code(answer.get("sql", ""), language="sql")
            st.markdown("**Definitions used**")
            st.json(answer.get("definitions_used") or {}, expanded=False)
            usage = result.get("usage") or {}
            st.caption(f"{answer.get('duration_ms', 0):.0f} ms in the database · "
                       f"{usage.get('llm_calls', 0)} model call(s) · {usage.get('input_tokens', 0)} tokens in, "
                       f"{usage.get('output_tokens', 0)} out")


def render_turn(turn_id: int, turn: dict) -> None:
    with st.chat_message("user"):
        st.text(turn["question"])
    with st.chat_message("assistant"):
        if turn.get("error"):
            st.error(turn["error"]["message"])
            return
        result = turn["result"]
        status = result["status"]
        if status == "ready" and result.get("answer"):
            st.caption(plain(view.describe_request(result["request"])))
            render_answer(turn_id, result)
        elif status == "needs_clarification":
            clar = result["clarification"]
            st.markdown("I need one detail first: " + plain(clar["question"]))
            for j, option in enumerate(clar.get("options") or []):
                if st.button(option, key=f"clar-{turn_id}-{j}"):
                    st.session_state.pending = f"{turn['question']} ({option})"
                    st.rerun()
        elif status == "not_supported":
            st.info(plain(result.get("reason") or "This question cannot be answered from the available data."),
                    icon=":material/block:")
        else:
            st.error("I could not turn that into a valid query. " + plain(result.get("reason") or ""))


def ask(question: str) -> None:
    turns = st.session_state.turns
    previous = view.next_previous_request(turns) if st.session_state.get("follow_up") else None
    try:
        with st.spinner("Working on it..."):
            result = get_api().ask(question, previous)
        turns.append({"question": question, "result": result})
    except ApiError as exc:
        turns.append({"question": question, "error": {"kind": exc.kind, "message": exc.message}})
    del turns[:-MAX_TURNS]


def main() -> None:
    st.set_page_config(page_title="Pagila analytics", page_icon=":material/analytics:", layout="wide")
    st.session_state.setdefault("turns", [])

    with st.sidebar:
        st.header("Pagila analytics")
        ok, status = get_api().ready()
        (st.success if ok else st.error)(status.capitalize() if ok else status)
        st.checkbox("Treat my question as a follow-up", value=True, key="follow_up",
                    help="Lets you say 'and by store?' after an answer.")
        st.checkbox("Show how answers were produced", key="show_details")
        if st.button("New conversation"):
            st.session_state.turns = []
            st.rerun()
        st.subheader("Try asking")
        for i, example in enumerate(EXAMPLES):
            if st.button(example, key=f"example-{i}", width="stretch"):
                st.session_state.pending = example

    st.title("Ask a question about the rental business")
    if not st.session_state.turns:
        st.write("Ask in plain English, for example: *revenue by month*, or *top 10 films by rentals*.")

    typed = st.chat_input("Ask a question", max_chars=500)
    question = st.session_state.pop("pending", None) or typed
    if question and question.strip():
        ask(question.strip())

    for i, turn in enumerate(st.session_state.turns):
        render_turn(i, turn)


main()
