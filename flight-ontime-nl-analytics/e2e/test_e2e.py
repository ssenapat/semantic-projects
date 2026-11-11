"""REQ-078: the six Spec §48 questions through Retriever -> Parser (stub LLM) [-> Query]."""
from __future__ import annotations

import json
import os

import pytest

from conftest import ACCEPTANCE, SIX_QUESTIONS


def ask(client, question: str):
    r = client.post("/api/v1/ask", json={"question": question})
    assert r.status_code == 200, r.text
    return r.json()


def no_sql(body) -> bool:
    return "SELECT " not in json.dumps(body).upper()


@pytest.mark.parametrize("key,outcome", [("delays", "clarification_required"), ("airline", "clarification_required"),
                                         ("chicago", "clarification_required"), ("pii", "unsupported")])
def test_question_outcome_without_db(stack_no_db, key, outcome):
    body = ask(stack_no_db, SIX_QUESTIONS[key])
    assert body["outcome"] == outcome
    assert body.get("result") is None
    assert no_sql(body)


def test_injection_question_yields_no_sql(stack_no_db):
    # REQ-078 expects "unsupported". CONTRACT 5.1 maps "no eligible document scored above 0" to 422
    # no_retrievable_context; either is a safe refusal. Anything else (a result, any SQL) fails.
    r = stack_no_db.post("/api/v1/ask", json={"question": SIX_QUESTIONS["injection"]})
    body = r.json()
    assert no_sql(body)
    if r.status_code == 200:
        assert body["outcome"] == "unsupported" and body.get("result") is None
    else:
        assert r.status_code == 422 and body["type"].endswith("no-retrievable-context")


def test_clarification_has_question(stack_no_db):
    assert ask(stack_no_db, SIX_QUESTIONS["delays"])["clarification"]["question"]


def test_error_envelope(stack_no_db):
    r = stack_no_db.post("/api/v1/ask", json={})
    assert r.status_code in (400, 422)
    assert set(r.json()) >= {"type", "title", "status", "detail", "request_id"}


@pytest.mark.db
def test_cancelled_flights_result_default_reference_date(db_stack):
    body = ask(db_stack, SIX_QUESTIONS["cancelled"])
    assert body["outcome"] == "result"      # may legitimately have zero rows (reference date 2026-01-31)
    assert "SELECT" not in json.dumps({k: v for k, v in body["result"].items() if k != "query_execution"}).upper()


@pytest.mark.db
def test_cancelled_flights_non_empty_with_reference_override(db_stack, monkeypatch):
    monkeypatch.setenv("E2E_REFERENCE_DATE", "2026-01-15")
    body = ask(db_stack, SIX_QUESTIONS["cancelled"])
    assert body["outcome"] == "result"
    assert body["result"]["rows"], "expected rows when last month lies inside the data window"


# ---------------------------------------------------------------------------------------------------------
# Multi-metric / multi-dimension acceptance examples (CONTRACT section 6), stub-LLM mode, in-memory fake DB.
# The fake DB proves the wiring and the response shape; values are asserted against Postgres in the db tests.
DR = {"kind": "relative", "relative_period": "last_complete_calendar_month"}


def _check_table_shape(result: dict, names: list[str], roles: list[str]):
    assert [c["name"] for c in result["columns"]] == names
    assert [c["role"] for c in result["columns"]] == roles
    assert isinstance(result["truncated"], bool) and result["truncated"] is False
    assert result["row_count"] == len(result["rows"]) > 0
    assert all(len(row) == len(names) for row in result["rows"])   # rows[i][j] belongs to columns[j] (C-4.5)
    assert list(result["metric_versions"]) == result["metadata"]["metrics"]
    assert len(result["metric_versions"]) == roles.count("metric")


def test_example_1_two_dimensions_one_metric(fake_stack):
    retriever, _query, _db = fake_stack
    body = ask(retriever, ACCEPTANCE[1])
    assert body["outcome"] == "result"
    _check_table_shape(body["result"], ["carrier_code", "origin_airport_code", "cancelled_flight_count"],
                       ["dimension", "dimension", "metric"])
    assert body["result"]["metadata"]["metrics"] == ["cancelled_flight_count"]


def test_example_2_three_metrics_one_dimension(fake_stack):
    retriever, _query, _db = fake_stack
    body = ask(retriever, ACCEPTANCE[2])
    assert body["outcome"] == "result"
    result = body["result"]
    _check_table_shape(result, ["carrier_code", "flight_count", "cancelled_flight_count", "cancellation_rate"],
                       ["dimension", "metric", "metric", "metric"])
    assert len({row[0] for row in result["rows"]}) == result["row_count"]   # one row per carrier
    assert len(result["metric_versions"]) == 3


def test_example_3_filter_is_applied(fake_stack):
    retriever, _query, db = fake_stack
    body = ask(retriever, ACCEPTANCE[3])
    assert body["outcome"] == "result"
    result = body["result"]
    _check_table_shape(result, ["carrier_code", "origin_airport_code", "flight_count", "cancelled_flight_count"],
                       ["dimension", "dimension", "metric", "metric"])
    applied = result["metadata"]["applied_filters"]
    assert any(f.get("dimension") == "origin_airport_code" and sorted(f.get("values", [])) == ["MDW", "ORD"]
               for f in applied), applied
    sql, params = db.data_statements()[-1]
    assert "%(" in sql and "'ORD'" not in sql and "'MDW'" not in sql      # values travel as parameters
    assert any(isinstance(v, (list, tuple)) and set(v) == {"ORD", "MDW"} or v in ("ORD", "MDW")
               for v in params.values()), params


def test_example_4_airport_clarification(stack_no_db):
    body = ask(stack_no_db, ACCEPTANCE[4])
    assert body["outcome"] == "clarification_required"
    assert body["clarification"]["ambiguity_type"] == "airport_or_location"
    assert body.get("result") is None and no_sql(body)


def test_example_5_airline_name_unsupported(stack_no_db):
    body = ask(stack_no_db, ACCEPTANCE[5])
    assert body["outcome"] == "unsupported" and body.get("result") is None and no_sql(body)


def test_example_6_single_metric_in_a_one_item_list(fake_stack):
    retriever, _query, _db = fake_stack
    body = ask(retriever, ACCEPTANCE[6])
    assert body["outcome"] == "result"
    _check_table_shape(body["result"], ["flight_count"], ["metric"])
    assert body["result"]["metadata"]["metrics"] == ["flight_count"]


def test_example_7_legacy_field_rejected_by_query_service(fake_stack):
    _retriever, query, _db = fake_stack
    version = query.get("/health/ready").json()["catalog_version"]
    payload = {"request_id": "legacy-1", "catalog_version": version,
               "semantic_request": {"metric": "cancelled_flight_count", "date_range": DR}}  # legacy-rejection
    r = query.post("/api/v1/query/execute", json=payload)
    assert r.status_code == 422 and r.json()["type"].endswith("/query-validation-failed")


def test_example_7_legacy_field_rejected_by_parser_model_output():
    from llm_intent_parser_service.app.services.structured_output_parser import parse_outcome
    legacy = {"outcome": "semantic_request", "catalog_version": "x", "confidence": 0.9,
              "semantic_request": {"metric": "cancelled_flight_count", "group_by": [], "filters": []}}  # legacy-rejection
    outcome, errors = parse_outcome(json.dumps(legacy))
    assert outcome is None and errors


# ---- rejected cases ------------------------------------------------------------------------------------
def _execute(query, **semantic_request):
    version = query.get("/health/ready").json()["catalog_version"]
    semantic_request.setdefault("date_range", DR)
    return query.post("/api/v1/query/execute", json={"request_id": "rej-1", "catalog_version": version,
                                                      "semantic_request": semantic_request})


def test_rejected_duplicate_metric(fake_stack):
    _r, query, db = fake_stack
    r = _execute(query, metrics=["flight_count", "flight_count"])
    assert r.status_code == 422 and r.json()["type"].endswith("/query-validation-failed")
    assert db.data_statements() == []


def test_rejected_six_metrics(fake_stack):
    _r, query, db = fake_stack
    names = ["flight_count", "cancelled_flight_count", "cancellation_rate", "a", "b", "c"]
    r = _execute(query, metrics=names)
    assert r.status_code == 422 and r.json()["type"].endswith("/query-validation-failed")
    assert db.data_statements() == []


def _parser_context(metrics: list[dict]) -> dict:
    dims = [{"name": "carrier_code", "label": "Carrier", "description": "Carrier", "type": "categorical",
             "allowed_operators": ["equals", "in"]},
            {"name": "cancellation_reason", "label": "Reason", "description": "Reason", "type": "categorical",
             "allowed_operators": ["equals", "in"]}]
    return {"request_id": "req-1", "catalog_version": "sp1-test", "selected_domains": ["flight_operations"],
            "metrics": metrics, "dimensions": dims, "glossary": [], "ambiguity_rules": [],
            "response_rules": ["Use only supplied names."]}


def _metric(name: str, dims: list[str]) -> dict:
    return {"name": name, "label": name, "description": name, "domain": "flight_operations",
            "time_dimension": "flight_date", "allowed_dimensions": dims, "allowed_time_grains": ["day"],
            "combinable_with": "flight_operations"}


def test_rejected_dimension_allowed_by_only_some_metrics_at_the_parser():
    """Synthetic context: cancellation_rate lacks cancellation_reason; the Parser names that metric."""
    from llm_intent_parser_service.app.schemas.context import LlmSemanticContext
    from llm_intent_parser_service.app.schemas.request import ParserOptions
    from llm_intent_parser_service.app.services.response_validator import validate_outcome
    from llm_intent_parser_service.app.services.structured_output_parser import parse_outcome

    ctx = LlmSemanticContext.model_validate(_parser_context([
        _metric("flight_count", ["carrier_code", "cancellation_reason"]),
        _metric("cancellation_rate", ["carrier_code"]),
    ]))
    output = {"outcome": "semantic_request", "catalog_version": "sp1-test", "confidence": 0.9,
              "semantic_request": {"metrics": ["flight_count", "cancellation_rate"],
                                   "group_by": [{"dimension": "cancellation_reason"}], "filters": [],
                                   "date_range": DR, "requested_limit": 500}}
    outcome, errors = parse_outcome(json.dumps(output))
    assert outcome is not None, errors
    verdict = validate_outcome(outcome, ctx, "q", ParserOptions())
    deterministic = verdict.deterministic
    assert deterministic is not None and deterministic.outcome == "unsupported"
    assert deterministic.unsupported.conflicting_metrics == ["cancellation_rate"]


def test_rejected_dimension_not_allowed_for_metric_at_the_compiler():
    """Reachable only with the draft-capable `reconciliation` audience (no such case exists through /ask)."""
    from pathlib import Path
    from semantic_layer.compiler import compile_request
    from semantic_layer.loader import load_catalog

    catalog = load_catalog(Path(os.environ["SEMANTIC_LAYER_DEFINITIONS_DIR"]))
    response = compile_request({"metrics": ["flight_count", "cancellation_rate"],
                                "dimensions": ["cancellation_reason"]}, catalog, audience="reconciliation")
    assert response.status == "unsupported"
    assert response.reason_code == "dimension_not_allowed_for_metric"
    assert "cancellation_rate" in response.message


# ---- determinism, ordering, truncation (REQ-026, REQ-027, REQ-048) ------------------------------------
def test_same_request_twice_gives_identical_result(fake_stack):
    retriever, _query, db = fake_stack
    first = ask(retriever, ACCEPTANCE[2])["result"]
    second = ask(retriever, ACCEPTANCE[2])["result"]
    for key in ("columns", "rows", "truncated", "row_count", "metric_versions"):
        assert first[key] == second[key], key
    statements = [sql for sql, _ in db.data_statements()]
    assert len(statements) == 2 and statements[0] == statements[1]
    # C-4.2: first metric descending (NULLs last), then each group_by dimension ascending.
    assert 'ORDER BY "flight_count" DESC NULLS LAST, "carrier_code" ASC' in statements[0]


def test_truncation_is_flagged_not_an_error(fake_stack):
    _r, query, _db = fake_stack
    r = _execute(query, metrics=["flight_count"],
                 group_by=[{"dimension": "carrier_code"}, {"dimension": "origin_airport_code"}], requested_limit=2)
    assert r.status_code == 200
    result = r.json()
    assert result["outcome"] == "result"
    assert result["truncated"] is True and result["row_count"] == 2 and len(result["rows"]) == 2
    again = _execute(query, metrics=["flight_count"],
                     group_by=[{"dimension": "carrier_code"}, {"dimension": "origin_airport_code"}],
                     requested_limit=2).json()
    for key in ("rows", "truncated", "row_count"):
        assert result[key] == again[key], key


# ---- with Postgres (skipped when PGPASSWORD is unset) --------------------------------------------------
@pytest.mark.db
def test_db_example_2_rate_is_numerator_over_denominator(db_stack):
    result = ask(db_stack, ACCEPTANCE[2])["result"]
    names = [c["name"] for c in result["columns"]]
    assert names == ["carrier_code", "flight_count", "cancelled_flight_count", "cancellation_rate"]
    assert len({row[0] for row in result["rows"]}) == len(result["rows"])
    for row in result["rows"]:
        values = dict(zip(names, row))
        if values["flight_count"]:
            assert abs(float(values["cancellation_rate"]) - values["cancelled_flight_count"] / values["flight_count"]) < 1e-9


@pytest.mark.db
def test_db_example_3_only_chicago_airports(db_stack):
    result = ask(db_stack, ACCEPTANCE[3])["result"]
    names = [c["name"] for c in result["columns"]]
    assert {row[names.index("origin_airport_code")] for row in result["rows"]} <= {"ORD", "MDW"}


@pytest.mark.db
def test_db_same_request_twice_is_identical(db_stack):
    a, b = ask(db_stack, ACCEPTANCE[1])["result"], ask(db_stack, ACCEPTANCE[1])["result"]
    for key in ("rows", "truncated", "row_count"):
        assert a[key] == b[key], key
