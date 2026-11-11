from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from analytics_client import view
from analytics_client.api import ApiError, IntentApi

APP = str(Path(__file__).parent.parent / "src" / "analytics_client" / "app.py")


def answer(request, rows, columns=None, caveats=(), truncated=False):
    return {"status": "ready", "reason": None, "request": request, "assumptions": ["Revenue is dated by the rental date."],
            "clarification": None, "usage": {"llm_calls": 1, "input_tokens": 10, "output_tokens": 5},
            "answer": {"model": request["model"], "columns": columns or list(rows[0]), "rows": rows, "row_count": len(rows),
                       "truncated": truncated, "sql": "SELECT 1", "definitions_used": {"model:payment_activity": "1.0.0"},
                       "caveats": list(caveats), "duration_ms": 12.0}}


TOTAL = answer({"model": "payment_activity", "measures": ["total_revenue"]}, [{"total_revenue": 170962.39}])
MONTHLY = answer({"model": "payment_activity", "measures": ["total_revenue"], "dimensions": ["rental_date:month"]},
                 [{"rental_date": "2025-01-01", "total_revenue": 10.0}, {"rental_date": "2025-02-01", "total_revenue": 20.0}],
                 caveats=["Data ends on 2026-07-28, so the most recent month may be partial."])


@pytest.fixture
def backend(monkeypatch):
    class Backend:
        calls = []
        replies = []
        ready = (True, "connected")
    b = Backend()
    b.calls, b.replies = [], []

    def ask(self, question, previous_request=None):
        b.calls.append((question, previous_request))
        reply = b.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply
    monkeypatch.setattr(IntentApi, "ask", ask)
    monkeypatch.setattr(IntentApi, "ready", lambda self: b.ready)
    return b


def run():
    return AppTest.from_file(APP, default_timeout=20).run()


def type_question(at, text):
    at.chat_input[0].set_value(text).run()
    assert not at.exception, at.exception
    return at


def test_empty_page_offers_examples_and_shows_connection(backend):
    at = run()
    assert not at.exception and at.success[0].value == "Connected"
    assert len(at.button) >= 6 and not at.chat_message


def test_single_number_is_a_metric_with_assumption(backend):
    backend.replies = [TOTAL]
    at = type_question(run(), "total revenue")
    assert at.metric[0].label == "total revenue" and at.metric[0].value == "170,962.39"
    assert "dated by the rental date" in at.info[0].value
    assert [m.name for m in at.chat_message] == ["user", "assistant"]


def test_series_gets_chart_table_and_caveat(backend):
    backend.replies = [MONTHLY]
    at = type_question(run(), "revenue by month")
    assert len(at.get("vega_lite_chart")) == 1 and len(at.dataframe) == 1
    assert any("may be partial" in c.value for c in at.caption)


def test_follow_up_sends_previous_request_and_toggle_disables_it(backend):
    backend.replies = [TOTAL, TOTAL, TOTAL]
    at = type_question(run(), "total revenue")
    type_question(at, "and by store?")
    assert backend.calls[0][1] is None and backend.calls[1][1] == TOTAL["request"]
    at.checkbox(key="follow_up").uncheck().run()
    type_question(at, "something else")
    assert backend.calls[2][1] is None


def test_new_conversation_clears(backend):
    backend.replies = [TOTAL]
    at = type_question(run(), "total revenue")
    at.button[[b.label for b in at.button].index("New conversation")].click().run()
    assert not at.chat_message


def test_example_button_asks_that_question(backend):
    backend.replies = [TOTAL]
    at = run()
    at.button(key="example-1").click().run()
    assert not at.exception and backend.calls[0][0] == "Top 10 films by number of rentals"


def test_clarification_buttons_ask_again_with_the_choice(backend):
    backend.replies = [{"status": "needs_clarification", "request": None, "assumptions": [], "reason": None,
                        "clarification": {"question": "Revenue or rentals?", "options": ["Revenue", "Rentals"]}}, TOTAL]
    at = type_question(run(), "how are we doing")
    assert "Revenue or rentals" in at.markdown[-1].value
    at.button(key="clar-0-0").click().run()
    assert not at.exception and backend.calls[1][0] == "how are we doing (Revenue)"


def test_not_supported_and_failed(backend):
    backend.replies = [{"status": "not_supported", "reason": "Profit data is not available.", "request": None},
                       {"status": "failed", "reason": "unknown measure", "request": None}]
    at = type_question(run(), "profit?")
    assert "Profit data" in at.info[0].value
    type_question(at, "gibberish")
    assert "could not turn that" in at.error[0].value


@pytest.mark.parametrize("exc", [ApiError("busy", "The service is busy. Please try again in a moment."),
                                 ApiError("unreachable", "Cannot reach the intent service.")])
def test_errors_are_plain_messages_and_the_page_survives(backend, exc):
    backend.replies = [exc, TOTAL]
    at = type_question(run(), "revenue")
    assert at.error[0].value == exc.message
    type_question(at, "total revenue")
    assert at.metric[0].value == "170,962.39"


def test_service_down_is_shown_in_the_sidebar(backend):
    backend.ready = (False, "Cannot reach the intent service at http://x. Is it running?")
    at = run()
    assert not at.exception and "Cannot reach" in at.error[0].value


def test_details_are_hidden_until_asked_for(backend):
    backend.replies = [TOTAL]
    at = type_question(run(), "total revenue")
    assert not at.expander
    at.checkbox(key="show_details").check().run()
    assert at.expander and at.code[0].value == "SELECT 1"


def test_model_text_cannot_render_images_or_links(backend):
    evil = dict(TOTAL, assumptions=["![x](http://evil.test/?q=secret) [click](http://evil.test)"], reason="![y](http://evil.test)")
    evil["answer"] = dict(TOTAL["answer"], caveats=["<img src=http://evil.test>"])
    backend.replies = [evil, {"status": "not_supported", "reason": "[a](http://evil.test)", "request": None}]
    at = type_question(run(), "total revenue")
    assert "![" not in at.info[0].value and "](" not in at.info[0].value
    assert "\\<img" in at.caption[-1].value
    type_question(at, "other")
    assert "](" not in at.info[-1].value


def test_truncation_is_flagged(backend):
    backend.replies = [answer({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["film_title"]},
                              [{"film_title": "A", "rental_count": 3}, {"film_title": "B", "rental_count": 2}], truncated=True)]
    at = type_question(run(), "films")
    assert any("first 2 rows" in w.value for w in at.warning)


def test_model_caption_and_exact_truncation_warning(backend):
    backend.replies = [answer({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["film_title"]},
                              [{"film_title": "A", "rental_count": 3}, {"film_title": "B", "rental_count": 2}], truncated=True)]
    at = type_question(run(), "films")
    assert any(c.value == "Model: rental_activity" for c in at.caption)
    assert any(w.value == "Only the first 2 rows are shown." for w in at.warning)


def test_table_has_one_column_per_field_and_chart_cap(backend):
    req = {"model": "payment_activity", "measures": ["total_revenue", "payment_count"], "dimensions": ["rental_date:month"]}
    cols = [{"name": "rental_date", "role": "dimension", "label": "Rental date", "unit": None, "type": "date"},
            {"name": "total_revenue", "role": "measure", "label": "Total revenue", "unit": "currency", "type": "number"},
            {"name": "payment_count", "role": "measure", "label": "Payment count", "unit": "count", "type": "number"}]
    rows = [{"rental_date": "2025-02-01", "total_revenue": 2.0, "payment_count": 1}, {"rental_date": "2025-01-01", "total_revenue": 1234.5, "payment_count": 2}]
    backend.replies = [answer(req, rows, columns=cols)]
    at = type_question(run(), "revenue per month")
    assert not at.exception
    assert list(at.dataframe[0].value.columns) == ["Rental date", "Total revenue", "Payment count"]
    assert len(at.dataframe[0].value) == 2
    assert len(view.chart_plans(req, view.answer_frame({"columns": cols, "rows": rows}, req))) <= view.MAX_CHARTS
