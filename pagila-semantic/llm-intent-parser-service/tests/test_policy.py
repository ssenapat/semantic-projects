"""Policy-driven tool schema, unsupported refusals, prompt rules and golden cases (no network, no key)."""
import json
from pathlib import Path

import pytest

from intent_parser.prompt import RULES, system_prompt, tools
from intent_parser.query_client import UpstreamError

from support import clarify, submit

GOLDEN = json.loads((Path(__file__).parent.parent / "evals" / "golden.json").read_text())
POLICY = {"max_measures_per_query": 3, "max_dimensions_per_query": 2, "default_limit": 50, "max_rows": 777,
          "max_joins": 10, "max_fan_out_dimensions": 1, "version": "9.9.9"}


def _submit_schema(catalog, policy):
    tool = next(t for t in tools(catalog, policy) if t["name"] == "submit_request")
    return tool["input_schema"]["properties"]


@pytest.fixture
def catalog(query_http):
    return query_http.get("/v1/catalog").json()


@pytest.fixture
def policy(query_http):
    return query_http.get("/v1/context").json()["policy"]


def test_tool_limits_come_from_the_policy_not_literals(catalog):
    props = _submit_schema(catalog, POLICY)
    assert props["measures"]["maxItems"] == 3
    assert props["dimensions"]["maxItems"] == 2
    assert props["limit"]["maximum"] == 777
    other = _submit_schema(catalog, {**POLICY, "max_measures_per_query": 5, "max_dimensions_per_query": 8,
                                     "max_rows": 10000})
    assert (other["measures"]["maxItems"], other["dimensions"]["maxItems"], other["limit"]["maximum"]) == (5, 8, 10000)


def test_limits_are_rendered_with_their_numbers(catalog):
    prompt = system_prompt(catalog, POLICY)
    assert "at most 3 measures" in prompt and "at most 2 dimensions" in prompt and "777" in prompt
    assert "LIMITS" in prompt


def test_parser_hands_the_live_policy_to_the_model(make_parser, policy):
    parser, llm = make_parser(clarify("Which?"))
    parser.parse("Activity by store")
    system, _, tool_defs = llm.calls[0]
    props = next(t for t in tool_defs if t["name"] == "submit_request")["input_schema"]["properties"]
    assert props["measures"]["maxItems"] == policy["max_measures_per_query"]
    assert props["dimensions"]["maxItems"] == policy["max_dimensions_per_query"]
    assert props["limit"]["maximum"] == policy["max_rows"]
    assert f"at most {policy['max_measures_per_query']} measures" in system


@pytest.mark.parametrize("drop", ["whole", "field"])
def test_missing_policy_fails_closed(make_parser, drop):
    parser, llm = make_parser(clarify("Which?"))
    orig = parser.queries.context

    def context(rid=None):
        ctx = dict(orig(rid))
        if drop == "whole":
            ctx.pop("policy", None)
        else:
            ctx["policy"] = {k: v for k, v in ctx["policy"].items() if k != "max_rows"}
        return ctx
    parser.queries.context = context
    with pytest.raises(UpstreamError):
        parser.parse("revenue by month")
    assert llm.calls == []                      # the model is never asked without limits


# What the real query service answers (HTTP 422, type unsupported, code mixed_models, CONTRACT 2.1) for this request.
UNSUPPORTED_MESSAGE = ("measures come from different models ('total_revenue' is in model 'payment_activity'; "
                       "'rental_count' is in model 'rental_activity'); they cannot be combined, ask as separate questions")


def test_unsupported_refusal_is_final_without_repair(make_parser):
    mixed = dict(model="payment_activity", measures=["total_revenue", "rental_count"],
                 dimensions=["rental_date:month"], assumptions=["x"])
    parser, llm = make_parser(submit(**mixed))      # a second call would fail the script assertion
    r = parser.parse("Revenue and rental count by month")
    assert r["status"] == "not_supported"
    assert r["reason"] == UNSUPPORTED_MESSAGE and r["request"] is None
    assert r["usage"]["llm_calls"] == 1 and len(llm.calls) == 1


def test_other_refusals_are_still_repaired(make_parser):
    bad = dict(model="payment_activity", measures=["no_such_measure"], assumptions=["x"])
    good = dict(model="payment_activity", measures=["total_revenue"], assumptions=["x"])
    parser, llm = make_parser(submit(**bad), submit(**good))
    r = parser.parse("revenue")
    assert r["status"] == "ready" and r["usage"]["llm_calls"] == 2


def test_rules_satisfy_contract_4_3():
    text = RULES.lower()
    assert "several measures" in text and "one model" in text            # (a)
    assert "never combined" in text and "two separate questions" in text  # (b)
    assert "payment_store" in RULES and "store_id" in RULES               # (c)
    assert "ask_clarification only when no requested measure fixes the model" in text
    assert "limits" in text                                               # (d), numbers rendered in system_prompt


def _by_q(q):
    return next(c for c in GOLDEN if c["q"] == q)


def test_golden_file_has_the_contract_cases():
    expect = {
        "Revenue and payment count by month": ("ready", "payment_activity", {"total_revenue", "payment_count"},
                                               {"rental_date:month"}),
        "Rentals and average rental days by category and rating": (
            "ready", "rental_activity", {"rental_count", "avg_rental_days"}, {"category", "rating"}),
        "Revenue by store and category": ("ready", "payment_activity", {"total_revenue"},
                                          {"payment_store", "payment_category"}),
        "Revenue and payment count by store and category": (
            "ready", "payment_activity", {"total_revenue", "payment_count"}, {"payment_store", "payment_category"}),
        "Revenue by store": ("ready", "payment_activity", {"total_revenue"}, {"payment_store"}),
    }
    for q, (status, model, measures, dims) in expect.items():
        c = _by_q(q)
        assert (c["status"], c["model"], set(c["measures"]), set(c["dimensions"])) == (status, model, measures, dims)
    for q in ("Revenue and rentals together by month", "Revenue and rental count by month"):
        assert _by_q(q)["status"] == "not_supported"
    assert _by_q("Activity by store")["status"] == "needs_clarification"


@pytest.mark.parametrize("case", [c for c in GOLDEN if c["status"] == "ready"], ids=lambda c: c["q"][:40])
def test_ready_golden_requests_compile(make_parser, case):
    """Each expected ready request is accepted by the real compiler (the model itself runs in -m live)."""
    request = dict(model=case["model"], measures=case["measures"], dimensions=case.get("dimensions", []),
                   assumptions=["a stated default"])
    if "limit" in case:
        request["limit"] = case["limit"]
    for dim in case.get("filters_on", []):
        request["filters"] = [{"dimension": dim, "op": "between", "value": ["2025-01-01", "2025-12-31"]}]
    parser, _ = make_parser(submit(**request))
    r = parser.parse(case["q"])
    assert r["status"] == "ready", r
    assert set(r["request"]["measures"]) == set(case["measures"])
    assert set(r["request"].get("dimensions", [])) == set(case.get("dimensions", []))


@pytest.mark.parametrize("q", ["Revenue and rentals together by month", "Revenue and rental count by month"])
def test_cross_model_golden_questions_end_as_not_supported(make_parser, q):
    mixed = dict(model="payment_activity", measures=["total_revenue", "rental_count"],
                 dimensions=["rental_date:month"], assumptions=["x"])
    parser, llm = make_parser(submit(**mixed))
    r = parser.parse(q)
    assert r["status"] == _by_q(q)["status"] == "not_supported" and r["usage"]["llm_calls"] == 1


def test_activity_by_store_clarifies(make_parser):
    parser, _ = make_parser(clarify("Which activity?", ["Revenue by store", "Rentals by store"]))
    r = parser.parse("Activity by store")
    assert r["status"] == "needs_clarification" and r["clarification"]["options"]
