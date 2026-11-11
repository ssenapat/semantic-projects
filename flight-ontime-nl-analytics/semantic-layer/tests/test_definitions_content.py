"""Content assertions on the shipped definitions (REQ-002, REQ-010, REQ-011, REQ-017, REQ-018,
REQ-022, REQ-026, REQ-033). Each ambiguity Q1-Q7 must be encoded in the file CONTRACT C-8.3 names.
"""
from __future__ import annotations

import pytest

EXPECTED_FILES = [
    "sources/postgres_sources.yml",
    "semantic_models/flight_operations.yml",
    "semantic_models/code_mappings.yml",
    "metrics/flight_operations_metrics.yml",
    "relationships/transport_relationships.yml",
    "policies/data_policy.yml",
    "policies/query_policy.yml",
]
EXPECTED_VALIDATION = ["data_coverage.sql", "flag_integrity.sql", "key_uniqueness.sql",
                       "metric_reconciliation.sql", "referential_integrity.sql"]

# name: (status, baseline, tolerance)  -- CONTRACT C-3.4
METRICS = {
    "flight_count": ("validated", 544003, 0),
    "operated_flight_count": ("validated", 518368, 0),
    "cancelled_flight_count": ("validated", 25635, 0),
    "cancellation_rate": ("validated", 0.0471, 0.0001),
    "diverted_flight_count": ("validated", 1146, 0),
    "diversion_rate": ("draft", 0.0022, 0.0001),
    "departure_delay_rate": ("validated", 0.2068, 0.0001),
    "arrival_delay_rate": ("validated", 0.2078, 0.0001),
    "on_time_arrival_rate": ("draft", 0.7922, 0.0001),
    "departure_delayed_flight_count": ("validated", 107297, 0),
    "arrival_delayed_flight_count": ("validated", 107475, 0),
}
MANDATORY = ("label", "description", "status", "version", "business_owner", "technical_owner", "source",
             "grain", "expression", "unit", "time_basis", "null_handling", "allowed_dimensions",
             "validation_sql", "reconciliation", "change_log")


def dims(catalog):
    return {d.name: d for d in catalog.models["flight_operations"].dimensions}


def test_expected_files_exist(definitions_dir, validation_dir):
    for rel in EXPECTED_FILES:
        assert (definitions_dir / rel).is_file(), rel
    for name in EXPECTED_VALIDATION:
        assert (validation_dir / name).is_file(), name


def test_every_definition_file_has_version_header(definitions_dir):
    for f in definitions_dir.rglob("*.yml"):
        assert f.read_text(encoding="utf-8").lstrip().startswith("version: 1"), f.name


def test_connection_targets_tran_stats_read_only(catalog):
    c = catalog.connection
    assert (c.engine, c.database, c.default_schema, c.credentials, c.read_only) == (
        "postgresql", "tran-stats", "ontime", "env", True)


# ------------------------------------------------------------------ metrics (REQ-010/011/018)
def test_metric_set_is_exactly_the_contract_catalog(catalog):
    assert set(catalog.metrics) == set(METRICS)


@pytest.mark.parametrize("name", sorted(METRICS))
def test_metric_status_baseline_tolerance(catalog, name):
    status, baseline, tol = METRICS[name]
    m = catalog.metrics[name]
    assert m.status == status
    assert m.reconciliation.baseline == baseline
    assert m.reconciliation.tolerance == tol


@pytest.mark.parametrize("name", sorted(METRICS))
def test_metric_has_every_mandatory_attribute(catalog, name):
    m = catalog.metrics[name]
    for attr in MANDATORY:
        value = getattr(m, attr)
        assert value not in (None, "", (), [], {}), f"{name}.{attr}"
    assert m.reconciliation.reference
    assert m.filters is not None
    if m.type == "ratio":
        assert m.numerator and m.denominator
    else:
        assert m.measure


@pytest.mark.parametrize("name", sorted(METRICS))
def test_no_metric_is_approved_without_an_approver(catalog, name):
    m = catalog.metrics[name]
    assert m.status != "approved", "nothing may be approved before business sign-off (REQ-028)"
    assert m.approval.status == "pending_business_signoff" and m.approval.approver is None


def test_ratio_metrics_state_null_exclusion(catalog):
    for name in ("departure_delay_rate", "arrival_delay_rate", "on_time_arrival_rate"):
        text = catalog.metrics[name].null_handling
        assert "NULL" in text and "excluded" in text, name


def test_change_log_ends_at_current_version(catalog):
    for m in catalog.metrics.values():
        assert m.change_log and m.change_log[-1].version == m.version


def test_measures_have_expected_base_names(catalog):
    names = {x.name for x in catalog.models["flight_operations"].measures}
    assert {"flight_count_base", "operated_count_base", "cancelled_count_base", "diverted_count_base",
            "dep_delayed_count_base", "arr_delayed_count_base", "dep_flagged_count_base",
            "arr_flagged_count_base", "arr_on_time_count_base"} <= names


# ------------------------------------------------------------------ ambiguities Q1-Q7 (REQ-026)
def test_q1_delay_direction_ambiguous_terms(catalog):
    rule = next(r for r in catalog.query_policy.ambiguous_terms if r.reason_code == "delay_direction_unspecified")
    assert {"delay_rate", "delayed_flights", "delays"} <= set(rule.terms)
    assert set(rule.options) == {"departure_delay_rate", "arrival_delay_rate",
                                 "departure_delayed_flight_count", "arrival_delayed_flight_count"}
    assert "delay_direction_unspecified" in catalog.query_policy.clarify_when


def test_q2_on_time_rate_is_draft_pending_owner(catalog):
    assert catalog.metrics["on_time_arrival_rate"].status == "draft"
    assert catalog.metrics["diversion_rate"].status == "draft"


def test_q3_departure_delay_rate_documents_cancelled_flags(catalog):
    m = catalog.metrics["departure_delay_rate"]
    assert m.open_question == "Q3" or "Q3" in str(m.open_question)
    assert any("cancelled" in c for c in m.caveats)


def test_q4_airport_role_ambiguity_and_role_playing_joins(catalog):
    rule = next(r for r in catalog.query_policy.ambiguous_terms if r.reason_code == "airport_role_unspecified")
    assert {"airport", "airport_code", "city", "state"} <= set(rule.terms)
    roles = {r.role for r in catalog.relationships.values() if r.role}
    assert roles == {"origin_airport", "destination_airport"}
    d = dims(catalog)
    assert d["origin_airport_code"].role == "origin_airport"
    assert d["destination_airport_code"].role == "destination_airport"
    assert d["destination_airport_code"].null_policy == "label_with_city_name"


def test_q5_carrier_names_have_no_guessed_mapping(catalog):
    m = catalog.mappings["carrier_display_names"]
    assert m.status == "draft" and dict(m.values) == {} and m.unmapped_policy == "show_code"
    rule = next(r for r in catalog.query_policy.unavailable_concepts if r.reason_code == "mapping_not_approved")
    assert {"carrier_name", "airline_name"} <= set(rule.terms)
    assert "mapping_not_approved" in catalog.query_policy.unsupported_when


@pytest.mark.parametrize("dim", ["distance_group", "departure_delay_group", "arrival_delay_group"])
def test_q6_group_dimensions_are_draft(catalog, dim):
    assert dims(catalog)[dim].status == "draft"


def test_q7_flight_date_is_the_time_basis(catalog):
    d = dims(catalog)["flight_date"]
    assert d.type == "time" and set(d.granularities) == {"day", "week", "month"}
    assert d.column == "fl_date"
    assert all(m.time_basis == "fl_date" for m in catalog.metrics.values())


def test_unavailable_concepts_cover_missing_data(catalog):
    rule = next(r for r in catalog.query_policy.unavailable_concepts if r.reason_code == "refers_to_unavailable_data")
    assert {"average_delay_minutes", "passengers", "revenue", "tail_number", "load_factor"} <= set(rule.terms)


# ------------------------------------------------------------------ relationships / sources (REQ-017)
def test_relationships_are_many_to_one_inner_from_fact(catalog):
    assert set(catalog.relationships) == {"flight_to_carrier", "flight_to_origin_airport",
                                          "flight_to_destination_airport"}
    for r in catalog.relationships.values():
        assert r.type == "many_to_one" and r.join == "inner" and r.left.startswith("flight_facts.")
        assert r.verified["row_loss"] == 0 and r.verified["duplication"] == 0


def test_prohibited_joins_declared(catalog):
    assert {(p.source, p.column) for p in catalog.prohibited_joins} == {
        ("airports", "code"), ("airports", "city_market_id")}


def test_sources_are_default_deny_classified(catalog):
    assert {s.physical_object for s in catalog.sources.values() if s.usage == "queryable"} == {
        "ontime.flight", "ontime.carrier", "ontime.airport"}
    assert all(s.classification for s in catalog.sources.values())


def test_no_exposed_column_matches_prohibited_patterns(catalog):
    from semantic_layer.policy import find_prohibited_columns
    cols = []
    for m in catalog.models.values():
        cols += [d.column for d in m.dimensions if getattr(d, "column", None)]
    assert find_prohibited_columns(catalog.data_policy.prohibited_column_patterns, cols) == []


def test_released_first_slice_references_known_objects(catalog):
    rel = catalog.releases["minimal_first_release"]
    assert set(rel.metrics) <= set(catalog.metrics)
    assert set(rel.dimensions) <= set(dims(catalog))
    assert all(catalog.metrics[m].status == "validated" for m in rel.metrics)
