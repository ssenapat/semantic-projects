import pytest

from semantic_query_service.app.schemas.semantic_request import (QueryClarificationResponse,
                                                                 QueryUnsupportedResponse)
from semantic_query_service.app.services.semantic_request_validator import (SemanticRequestValidator,
                                                                            ValidatedParts)


def run(registry, make_req, sr):
    return SemanticRequestValidator(registry).validate(make_req(sr))


def reason(out):
    assert isinstance(out, QueryUnsupportedResponse), out
    return out.unsupported.reason


def test_valid(registry, make_req):
    out = run(registry, make_req, {"metrics": ["cancellation_rate"], "group_by": [{"dimension": "carrier_code"}]})
    assert isinstance(out, ValidatedParts) and out.metrics[0].name == "cancellation_rate"


def test_unknown_metric(registry, make_req):
    assert reason(run(registry, make_req, {"metrics": ["nope"]})) == "no_approved_metric"


def test_draft_metric_not_eligible_for_internal(registry, make_req):
    assert registry.metric("diversion_rate") is None
    assert reason(run(registry, make_req, {"metrics": ["diversion_rate"]})) == "no_approved_metric"


def test_unknown_or_ineligible_dimension(registry, make_req):
    assert reason(run(registry, make_req, {"metrics": ["flight_count"],
                                           "group_by": [{"dimension": "pilot"}]})) == "no_supported_dimension"
    # draft dimension is not exposable at the internal audience
    assert reason(run(registry, make_req, {"metrics": ["flight_count"],
                  "group_by": [{"dimension": "cancellation_reason"}]})) == "no_supported_dimension"


@pytest.mark.parametrize("grain", ["quarter", "year"])
def test_unsupported_grain(registry, make_req, grain):
    assert reason(run(registry, make_req, {"metrics": ["flight_count"], "group_by": [
        {"dimension": "flight_date", "time_grain": grain}]})) == "unsupported_operation"


def test_grain_on_non_time_dimension(registry, make_req):
    assert reason(run(registry, make_req, {"metrics": ["flight_count"], "group_by": [
        {"dimension": "carrier_code", "time_grain": "month"}]})) == "unsupported_operation"


def test_contains_and_operator_by_type(registry, make_req):
    f = lambda d, op, v: run(registry, make_req, {"metrics": ["flight_count"], "filters": [
        {"dimension": d, "operator": op, "values": v}]})
    assert reason(f("carrier_code", "contains", ["A"])) == "unsupported_operation"
    assert reason(f("flight_date", "in", ["2026-01-01"])) == "unsupported_operation"
    assert reason(f("carrier_code", "between", ["A", "B"])) == "unsupported_operation"
    assert reason(f("flight_date", "between", ["2026-01-01"])) == "unsupported_operation"
    assert isinstance(f("flight_date", "between", ["2026-01-01", "2026-01-31"]), ValidatedParts)


def test_group_by_and_filter_limits(registry, make_req):
    dims = ["flight_date", "day_of_week", "month", "quarter", "carrier_code", "origin_airport_code"]
    assert reason(run(registry, make_req, {"metrics": ["flight_count"],
                  "group_by": [{"dimension": d} for d in dims]})) == "unsupported_operation"
    flt = [{"dimension": "carrier_code", "operator": "equals", "values": ["AA"]}] * 11
    assert reason(run(registry, make_req, {"metrics": ["flight_count"], "filters": flt})) == "unsupported_operation"


def test_sp1_ambiguous_term_clarifies(registry, make_req):
    out = run(registry, make_req, {"metrics": ["delay_rate"]})
    assert isinstance(out, QueryClarificationResponse)
    assert "departure_delay_rate" in out.clarification.candidate_options


def test_unavailable_concept_insufficient_context(registry, make_req):
    assert reason(run(registry, make_req, {"metrics": ["passengers"]})) == "insufficient_context"


def test_extra_fields_rejected_by_model(make_req):
    with pytest.raises(Exception):
        make_req({"metrics": ["flight_count"], "raw_sql": "select 1"})
