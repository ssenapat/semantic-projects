"""Multi-metric parser behaviour (REQ-042 seven shapes, REQ-001..006, REQ-009, REQ-012..021, REQ-035).

Contexts here are synthetic (no Retriever snapshot); fixture-backed cases live in test_api.py.
"""
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from llm_intent_parser_service.app import policy_limits
from llm_intent_parser_service.app.main import create_app
from llm_intent_parser_service.app.schemas.context import LlmSemanticContext
from llm_intent_parser_service.app.schemas.request import ParserOptions
from llm_intent_parser_service.app.schemas.response import (ConstrainedSemanticRequest, UnsupportedDetails)
from llm_intent_parser_service.app.services.response_validator import validate_outcome
from llm_intent_parser_service.app.services.structured_output_parser import parse_outcome

from .conftest import CATALOG, body, fixture_for, make_context, stub_settings

URL = "/api/v1/intent/parse"
DR = {"kind": "relative", "relative_period": "last_complete_calendar_month"}


def metric(name, dims, tag="flight_operations"):
    return {"name": name, "label": name.replace("_", " ").title(), "description": f"{name} description",
            "domain": "flight_operations", "time_dimension": "flight_date", "allowed_dimensions": dims,
            "allowed_time_grains": ["day", "month"], "combinable_with": tag}


def ctx_multi() -> dict:
    ctx = make_context()
    ctx["metrics"] = [
        metric("flight_count", ["flight_date", "origin_airport_code", "carrier_code"]),
        metric("cancelled_flight_count", ["flight_date", "origin_airport_code", "carrier_code"]),
        metric("cancellation_rate", ["flight_date", "carrier_code"]),          # no origin_airport_code
        metric("avg_delay", ["flight_date", "carrier_code"], tag="delay"),      # other group
        metric("untagged", ["flight_date", "carrier_code"], tag=None),
    ]
    return LlmSemanticContext.model_validate(ctx).model_dump(mode="json")


def req(metrics, group_by=(), filters=(), **extra):
    return {"outcome": "semantic_request", "catalog_version": CATALOG, "confidence": 0.9,
            "semantic_request": {"metrics": metrics, "group_by": [{"dimension": d} for d in group_by],
                                 "filters": list(filters), "date_range": DR, "requested_limit": 500, **extra}}


def check(output, question="q", ctx=None, **opts):
    ctx = LlmSemanticContext.model_validate(ctx or ctx_multi())
    outcome, errors = parse_outcome(json.dumps(output))
    assert outcome is not None, errors
    return validate_outcome(outcome, ctx, question, ParserOptions(**opts))


def api(tmp_path, output, ctx=None, question="q1"):
    c = TestClient(create_app(stub_settings(tmp_path, [fixture_for(question, output)])))
    return c, c.post(URL, json=body(question, ctx=ctx or ctx_multi()))


# ---- the seven REQ-042 shapes -------------------------------------------------------------------
def test_1_one_item_metrics_accepted():
    r = check(req(["cancelled_flight_count"], ["carrier_code"]))
    assert r.ok and r.deterministic is None


def test_2_legacy_metric_field_rejected():
    out, errors = parse_outcome(json.dumps(
        {"outcome": "semantic_request", "catalog_version": CATALOG, "confidence": 0.9,
         "semantic_request": {"metric": "flight_count", "group_by": [], "filters": []}}))
    assert out is None and any("metric: extra_forbidden" in e for e in errors)


def test_2b_legacy_metric_is_422_through_the_api(tmp_path):
    legacy = req(["flight_count"])
    legacy["semantic_request"]["metric"] = "flight_count"
    _, r = api(tmp_path, legacy)
    assert r.status_code == 422 and r.json()["type"].endswith("/invalid-model-output")


def test_3_several_metrics_accepted():
    r = check(req(["flight_count", "cancelled_flight_count"], ["carrier_code", "origin_airport_code"]))
    assert r.ok and r.deterministic is None


def test_4_duplicate_metrics_rejected():
    out, errors = parse_outcome(json.dumps(req(["flight_count", "flight_count"])))
    assert out is None and any(e.endswith("metrics: value_error") for e in errors)
    with pytest.raises(ValidationError, match="metrics must be unique"):
        ConstrainedSemanticRequest(metrics=["flight_count", "flight_count"])


def test_5_dimension_not_allowed_for_one_metric_names_the_metric(tmp_path):
    r = check(req(["flight_count", "cancellation_rate"], ["origin_airport_code"]))
    d = r.deterministic
    assert d is not None and d.outcome == "unsupported" and d.confidence == 1.0
    assert d.unsupported.reason == "no_supported_dimension"
    assert d.unsupported.conflicting_metrics == ["cancellation_rate"]
    assert d.unsupported.message == "Dimension 'origin_airport_code' is not available for: cancellation_rate."
    assert d.unsupported.suggested_supported_topics == []
    _, resp = api(tmp_path, req(["flight_count", "cancellation_rate"], ["origin_airport_code"]))
    assert resp.status_code == 200 and resp.json()["unsupported"]["conflicting_metrics"] == ["cancellation_rate"]


def test_6_ambiguous_airport_gives_clarification():
    ctx = ctx_multi()
    ctx["ambiguity_rules"] = [{"trigger": "airport", "alternatives": [],
                               "required_action": "clarify", "question_template": "Which airport?"}]
    ctx = LlmSemanticContext.model_validate(ctx).model_dump(mode="json")
    r = check(req(["cancelled_flight_count"], ["origin_airport_code"]), question="Cancelled flights by airport",
              ctx=ctx)
    assert any("clarification trigger" in e for e in r.errors)          # a request is refused ...
    clar = {"outcome": "clarification_required", "catalog_version": CATALOG, "confidence": 0.8,
            "clarification": {"question": "Which airport?", "ambiguity_type": "airport_or_location",
                              "candidate_options": ["origin airport", "destination airport", "both"]}}
    assert check(clar, question="Cancelled flights by airport", ctx=ctx).ok  # ... and the clarification passes


def test_7_unsupported_terms_give_unsupported():
    out = {"outcome": "unsupported", "catalog_version": CATALOG, "confidence": 0.9,
           "unsupported": {"message": "Carrier display names are not available.", "reason": "no_supported_dimension",
                           "suggested_supported_topics": [], "conflicting_metrics": []}}
    assert check(out).ok


# ---- schema rules (C-2.1, C-2.2, C-4.1) ---------------------------------------------------------
def test_max_metrics_is_single_sourced():
    from llm_intent_parser_service.app.schemas import response
    assert response.MAX_METRICS is policy_limits.MAX_METRICS
    assert policy_limits.MAX_METRICS == policy_limits.load_max_metrics_per_query() == 5
    # the compiler/policy value: the parser must not import semantic_layer, so read the same policy file directly
    import yaml
    policy = yaml.safe_load(policy_limits._policy_path(None).read_text(encoding="utf-8"))
    assert policy["max_metrics_per_query"] == policy_limits.MAX_METRICS


def test_policy_reader_errors(tmp_path):
    with pytest.raises(RuntimeError, match="max_metrics_per_query"):
        policy_limits.load_max_metrics_per_query(tmp_path)  # file missing
    (tmp_path / "policies").mkdir()
    f = tmp_path / "policies" / "query_policy.yml"
    for text in ("other: 1\n", "max_metrics_per_query: 0\n", "max_metrics_per_query: abc\n"):
        f.write_text(text, encoding="utf-8")
        with pytest.raises(RuntimeError, match="max_metrics_per_query"):
            policy_limits.load_max_metrics_per_query(tmp_path)
    f.write_text("max_metrics_per_query: 7\n", encoding="utf-8")
    assert policy_limits.load_max_metrics_per_query(tmp_path) == 7


@pytest.mark.parametrize("metrics,ok", [(["a"], True), (["a", "b", "c", "d", "e"], True), ([], False),
                                        (list("abcdef"), False), (["a", "a"], False)])
def test_metrics_bounds(metrics, ok):
    if ok:
        ConstrainedSemanticRequest(metrics=metrics)
    else:
        with pytest.raises(ValidationError):
            ConstrainedSemanticRequest(metrics=metrics)


def test_missing_metrics_and_legacy_key_rejected():
    for payload in ({}, {"metric": "a"}, {"metrics": ["a"], "metric": "a"}):
        with pytest.raises(ValidationError):
            ConstrainedSemanticRequest(**payload)


def test_group_by_bounds_and_uniqueness():
    g = lambda d, t=None: {"dimension": d, "time_grain": t}
    ConstrainedSemanticRequest(metrics=["a"], group_by=[g(f"d{i}") for i in range(8)])
    with pytest.raises(ValidationError):
        ConstrainedSemanticRequest(metrics=["a"], group_by=[g(f"d{i}") for i in range(9)])
    with pytest.raises(ValidationError):
        ConstrainedSemanticRequest(metrics=["a"], group_by=[g("d"), g("d")])
    ConstrainedSemanticRequest(metrics=["a"], group_by=[g("flight_date", "day"), g("flight_date", "month")])
    ConstrainedSemanticRequest(metrics=["a"], group_by=[g("flight_date", "day"), g("flight_date")])


def test_unsupported_details_conflicting_metrics_and_reason():
    d = UnsupportedDetails(message="m", reason="metrics_not_combinable")
    assert d.conflicting_metrics == [] and d.model_dump()["conflicting_metrics"] == []
    with pytest.raises(ValidationError):
        UnsupportedDetails(message="m", reason="metrics_not_combinable", conflicting_metrics=list("abcdef"))


def test_llm_safe_metric_combinable_with_defaults_to_none():
    ctx = make_context()
    del ctx["metrics"][0]["combinable_with"]
    assert LlmSemanticContext.model_validate(ctx).metrics[0].combinable_with is None


# ---- validator rules (C-2.4.1) ------------------------------------------------------------------
def test_step3_unknown_metrics_listed_in_request_order(tmp_path):
    r = check(req(["flight_count", "ghost_a", "ghost_b"]))
    u = r.deterministic.unsupported
    assert u.reason == "no_approved_metric" and u.conflicting_metrics == []
    assert u.message == "These metrics are not available in the approved context: ghost_a, ghost_b."
    c, resp = api(tmp_path, req(["ghost_a"]))
    assert resp.status_code == 200 and resp.json()["outcome"] == "unsupported"
    assert "intent_parser_model_retry_total 1" not in c.get("/metrics").text


def test_step4_not_combinable_lists_conflicting_in_order():
    r = check(req(["flight_count", "avg_delay", "cancelled_flight_count"]))
    u = r.deterministic.unsupported
    assert u.reason == "metrics_not_combinable" and u.conflicting_metrics == ["flight_count", "avg_delay"]
    assert u.message == "These metrics cannot be combined in one query: flight_count, avg_delay."


def test_step4_first_metric_untagged_lists_all():
    u = check(req(["untagged", "flight_count"])).deterministic.unsupported
    assert u.conflicting_metrics == ["untagged", "flight_count"]


def test_step4_untagged_later_metric_is_conflicting():
    u = check(req(["flight_count", "untagged"])).deterministic.unsupported
    assert u.conflicting_metrics == ["flight_count", "untagged"]


def test_single_untagged_metric_is_fine():
    assert check(req(["untagged"], ["carrier_code"])).ok


def test_step5b_every_metric_blocks_is_kind_a_naming_metrics():
    r = check(req(["flight_count", "cancelled_flight_count"], ["destination_city"]))
    assert r.deterministic is None and any("is not in the semantic context" in e for e in r.errors)
    ctx = ctx_multi()
    ctx["dimensions"].append({"name": "x_dim", "label": "X", "description": "x", "type": "categorical",
                              "allowed_operators": ["equals"]})
    r = check(req(["flight_count", "cancelled_flight_count"], ["x_dim"]), ctx=ctx)
    assert r.errors == ["group_by dimension 'x_dim' is not allowed for metrics 'flight_count', "
                        "'cancelled_flight_count'"]
    r = check(req(["flight_count"], ["x_dim"]), ctx=ctx)
    assert r.errors == ["group_by dimension 'x_dim' is not allowed for metric 'flight_count'"]


def test_step5c_applies_to_filters_too():
    f = [{"dimension": "origin_airport_code", "operator": "equals", "values": ["ORD"]}]
    u = check(req(["flight_count", "cancellation_rate"], filters=f)).deterministic.unsupported
    assert u.reason == "no_supported_dimension" and u.conflicting_metrics == ["cancellation_rate"]


def test_kind_b_is_not_allowed_when_unsupported_disallowed_becomes_kind_a():
    r = check(req(["ghost"]), allow_unsupported=False)
    assert r.deterministic is None
    assert r.errors == ["These metrics are not available in the approved context: ghost."]


def test_kind_b_discarded_on_catalog_mismatch_or_leakage():
    wrong = req(["ghost"])
    wrong["catalog_version"] = "stale"
    r = check(wrong)
    assert r.deterministic is None and not r.ok
    r = check(req(["ghost; DROP TABLE x"]))
    assert r.deterministic is None and r.leakage and not r.ok


def test_message_cut_to_500_and_never_rejected_for_length():
    names = [f"ghost_{i}_" + "x" * 150 for i in range(3)]
    d = check(req(names)).deterministic
    assert d is not None and len(d.unsupported.message) == 500


def test_group_by_dimension_with_different_grains_for_same_dimension_ok():
    out = req(["flight_count"])
    out["semantic_request"]["group_by"] = [{"dimension": "flight_date", "time_grain": "day"},
                                           {"dimension": "flight_date", "time_grain": "month"}]
    assert check(out).ok


def test_time_grain_rule_unchanged():
    out = req(["flight_count"])
    out["semantic_request"]["group_by"] = [{"dimension": "flight_date", "time_grain": "year"}]
    assert any("time_grain 'year'" in e for e in check(out).errors)


def test_llm_unsupported_conflicting_metrics_must_be_context_metrics():
    out = {"outcome": "unsupported", "catalog_version": CATALOG, "confidence": 0.9,
           "unsupported": {"message": "no", "reason": "metrics_not_combinable", "suggested_supported_topics": [],
                           "conflicting_metrics": ["flight_count", "nope"]}}
    assert check(out).errors == ["conflicting_metrics entry 'nope' is not a metric of the context"]


def test_carrier_is_not_a_clarify_trigger_but_unqualified_airport_is():
    ctx = ctx_multi()
    ctx["ambiguity_rules"] = [{"trigger": "airport", "alternatives": [],
                               "required_action": "clarify", "question_template": "Which airport?"}]
    ctx = LlmSemanticContext.model_validate(ctx).model_dump(mode="json")
    ok = req(["flight_count"], ["carrier_code"])
    assert check(ok, question="Flights by carrier", ctx=ctx).ok
    assert not check(ok, question="Flights by airport", ctx=ctx).ok
    assert check(ok, question="Flights by origin airport", ctx=ctx).ok
