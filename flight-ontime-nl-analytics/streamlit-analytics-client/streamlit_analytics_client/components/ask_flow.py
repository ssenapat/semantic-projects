from __future__ import annotations

import streamlit as st

from ..models import session_models as sm
from ..utils.errors import user_message
from ..utils import observability
from ..utils.session import add_context, add_history, init_session
from .api_client import ApiError, make_client
from .result_view import render_response


def _ask(question: str) -> None:
    state = st.session_state
    observability.count_question(state)
    try:
        resp = make_client().ask(question, state[sm.KEY_CONVERSATION])
    except ApiError as e:
        st.error(user_message(e.status, e.request_id))
        return
    state[sm.KEY_LAST_RESPONSE] = resp
    state[sm.KEY_CHART] = None
    if resp.get("outcome") == "clarification_required":
        state[sm.KEY_PENDING] = {"question": question, "clarification": resp.get("clarification") or {}}
    else:
        state[sm.KEY_PENDING] = None
        add_context(state, question)
    add_history(state, question, resp)


def render_page() -> None:
    state = st.session_state
    init_session(state)
    st.title("Airline Analytics Assistant")
    with st.sidebar:
        st.subheader("Try asking")
        for i, example in enumerate(sm.EXAMPLE_QUESTIONS):
            if st.button(example, key=f"chip_{i}", width="stretch"):
                state[sm.KEY_QUEUED_QUESTION] = example
    queued = state.pop(sm.KEY_QUEUED_QUESTION, None)
    if queued:
        _ask(queued)
    with st.form("ask_form"):
        q = st.text_input("Your question", max_chars=4000, key="question_input")
        submitted = st.form_submit_button("Ask")
    if submitted:
        if not q.strip():
            st.error(user_message(400))
        else:
            _ask(q.strip())
    pending = state[sm.KEY_PENDING]
    if pending:
        options = pending["clarification"].get("candidate_options") or []
        st.info(pending["clarification"].get("question", ""))
        for i, opt in enumerate(options):
            if st.button(str(opt), key=f"clar_{i}"):
                original = pending["question"]
                add_context(state, f"{original} Selected: {opt}.")
                state[sm.KEY_PENDING] = None
                _ask(original)
                break
    pending = state[sm.KEY_PENDING]
    last = state[sm.KEY_LAST_RESPONSE]
    if last and not (pending and last.get("outcome") == "clarification_required"):
        render_response(last)
