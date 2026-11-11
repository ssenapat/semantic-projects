from datetime import date

from semantic_query_service.app.schemas.query_plan import ValidatedQueryPlan
from semantic_query_service.app.schemas.semantic_request import (QueryClarificationResponse,
                                                                 QueryUnsupportedResponse)
from semantic_query_service.app.services.query_planner import build_plan
from semantic_query_service.app.repositories.mapping_repository import MappingRepository
from semantic_query_service.app.services import internal_catalog_loader

R = date(2026, 1, 31)


def plan(registry, mappings, make_req, sr):
    return build_plan(make_req(sr), registry, mappings, R)


def test_catalog_versions(internal):
    v = internal.versions
    assert v.catalog_version == "sp1-" + internal.catalog.definitions_version
    assert v.source_checksum == internal.catalog.definitions_version
    assert internal_catalog_loader.compute_versions(internal.catalog).semantic_version == v.semantic_version


def test_mapping_loaded_flag_and_queries(fetch):
    seen = []
    m = MappingRepository(always_clarify=[])
    assert not m.loaded
    m.load(lambda s, p: (seen.append(s), fetch(s, p))[1])
    assert m.loaded
    assert seen == ["SELECT DISTINCT code, city_name, state_abr FROM ontime.airport",
                    "SELECT DISTINCT unique_carrier FROM ontime.carrier"]


def test_happy_plan(registry, mappings, make_req):
    p = plan(registry, mappings, make_req, {
        "metrics": ["flight_count"],
        "group_by": [{"dimension": "flight_date", "time_grain": "month"},
                     {"dimension": "carrier_code"}],
        "filters": [{"dimension": "origin_airport_code", "operator": "in", "values": ["ord", "Seattle"]},
                    {"dimension": "day_of_week", "operator": "equals", "values": ["Monday"]}],
        "date_range": {"kind": "relative", "relative_period": "last_complete_calendar_month"},
        "requested_limit": 5000})
    assert isinstance(p, ValidatedQueryPlan)
    assert p.metrics == ["flight_count"] and p.metric_versions == {"flight_count": 1} and p.source_model == "flight_operations"
    assert [g["sp1_term"] for g in p.group_by] == ["flight_date__month", "carrier_code"]
    assert p.resolved_filters[0] == {"dimension": "origin_airport_code", "operator": "in",
                                     "value": ["ORD", "SEA"]}
    assert p.resolved_filters[1] == {"dimension": "day_of_week", "operator": "eq", "value": 1}
    assert (p.resolved_date_range.start_inclusive, p.resolved_date_range.end_exclusive) == (
        "2025-12-01", "2026-01-01")
    assert p.requested_limit == registry.query_policy.max_result_rows


def test_day_grain_plain_term(registry, mappings, make_req):
    p = plan(registry, mappings, make_req, {"metrics": ["flight_count"],
             "group_by": [{"dimension": "flight_date", "time_grain": "day"}]})
    assert p.group_by[0]["sp1_term"] == "flight_date"


def test_chicago_always_clarifies(registry, mappings, make_req):
    for val in ("Chicago", "chicago"):
        p = plan(registry, mappings, make_req, {"metrics": ["flight_count"], "filters": [
            {"dimension": "origin_airport_code", "operator": "equals", "values": [val]}]})
        assert isinstance(p, QueryClarificationResponse)
        c = p.clarification
        assert c.ambiguity_type == "airport_or_location" and c.candidate_options == ["MDW", "ORD"]


def test_multi_airport_city_and_single_and_unknown(registry, mappings, make_req):
    def f(v):
        return plan(registry, mappings, make_req, {"metrics": ["flight_count"], "filters": [
            {"dimension": "destination_airport_code", "operator": "equals", "values": [v]}]})
    c = f("New York")
    assert isinstance(c, QueryClarificationResponse) and c.clarification.candidate_options == ["JFK", "LGA"]
    assert f("Atlanta").resolved_filters[0]["value"] == "ATL"
    u = f("Atlantis")
    assert isinstance(u, QueryUnsupportedResponse) and u.unsupported.reason == "insufficient_context"


def test_carrier_exact_code_only(registry, mappings, make_req):
    ok = plan(registry, mappings, make_req, {"metrics": ["flight_count"], "filters": [
        {"dimension": "carrier_code", "operator": "equals", "values": ["dl"]}]})
    assert ok.resolved_filters[0]["value"] == "DL"
    bad = plan(registry, mappings, make_req, {"metrics": ["flight_count"], "filters": [
        {"dimension": "carrier_code", "operator": "equals", "values": ["Delta Air Lines"]}]})
    assert isinstance(bad, QueryUnsupportedResponse) and bad.unsupported.reason == "insufficient_context"


def test_invalid_absolute_range_unsupported(registry, mappings, make_req):
    u = plan(registry, mappings, make_req, {"metrics": ["flight_count"], "date_range": {
        "kind": "absolute", "start_date": "2026-03-01", "end_date": "2026-01-01"}})
    assert isinstance(u, QueryUnsupportedResponse) and u.unsupported.reason == "unsupported_operation"


def test_limit_clamped_to_policy(registry, mappings, make_req):
    p = plan(registry, mappings, make_req, {"metrics": ["flight_count"], "requested_limit": 7})
    assert p.requested_limit == 7
