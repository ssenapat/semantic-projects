"""Service-side multi-metric cases (REQ-048): combinable / not combinable / dimension not allowed /
duplicates / truncation / deterministic ordering. Cites CONTRACT C-2.1, C-2.5, C-3.3, C-3.4, C-4.2-C-4.6."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from semantic_query_service.app import policy_limits
from semantic_query_service.app.schemas.semantic_request import (ConstrainedSemanticRequest,
                                                                 QueryExecuteRequest)
from semantic_query_service.app.services.semantic_request_validator import (SemanticRequestValidator,
                                                                           ValidatedParts)

DEFS = (Path(__file__).resolve().parents[2] / "semantic-layer" / "definitions").resolve()


# ---------------------------------------------------------------- helpers
@pytest.fixture(scope="module")
def internal():
    from semantic_query_service.app.services import internal_catalog_loader
    return internal_catalog_loader.load(str(DEFS))


@pytest.fixture()
def registry(internal):
    from semantic_query_service.app.repositories.internal_semantic_registry import InternalSemanticRegistry
    return InternalSemanticRegistry(internal, "internal")


def validate(registry, **sr):
    req = QueryExecuteRequest(request_id="r1", catalog_version="v", semantic_request=sr)
    return SemanticRequestValidator(registry).validate(req)


def _unsupported(out):
    assert out.outcome == "unsupported", out
    return out.unsupported


# ---------------------------------------------------------------- C-2.1 schema
def test_schema_accepts_one_to_max_unique_metrics():
    n = policy_limits.MAX_METRICS
    assert n == 5
    for k in (1, n):
        ConstrainedSemanticRequest(metrics=[f"m{i}" for i in range(k)])


@pytest.mark.parametrize("payload", [
    {"metrics": []},
    {"metrics": ["a", "b", "c", "d", "e", "f"]},
    {"metrics": ["a", "a"]},                                           # duplicate (C-2.1.c)
    {},                                                                # missing (C-2.1.g)
    {"metric": "flight_count"},                                        # legacy field (C-2.1.f)
    {"metric": "flight_count", "metrics": ["flight_count"]},
    {"metrics": ["a"], "group_by": [{"dimension": "d"}, {"dimension": "d"}]},          # C-2.1.e
    {"metrics": ["a"], "group_by": [{"dimension": f"d{i}"} for i in range(9)]},        # C-2.1.d
])
def test_schema_rejects(payload):
    with pytest.raises(ValidationError):
        ConstrainedSemanticRequest(**payload)


def test_schema_group_by_same_dimension_different_grain_ok():
    ConstrainedSemanticRequest(metrics=["a"], group_by=[{"dimension": "d", "time_grain": "day"},
                                                        {"dimension": "d", "time_grain": "month"},
                                                        {"dimension": "d"}])


def test_legacy_metric_is_422_over_http(client, body):
    b = body()
    b["semantic_request"] = {"metric": "flight_count"}
    r = client.post("/api/v1/query/execute", json=b)
    assert r.status_code == 422 and r.json()["type"].endswith("query-validation-failed")


# ---------------------------------------------------------------- C-3.3 policy reader
def test_policy_limits_reads_query_policy(internal, tmp_path):
    assert policy_limits.MAX_METRICS == internal.catalog.query_policy.max_metrics_per_query
    assert policy_limits.load_max_metrics_per_query(DEFS) == 5
    for text in ("default_limit: 1\n", "max_metrics_per_query: 0\n", "max_metrics_per_query: x\n"):
        d = tmp_path / "defs" / "policies"
        d.mkdir(parents=True, exist_ok=True)
        (d / "query_policy.yml").write_text(text)
        with pytest.raises(RuntimeError, match="max_metrics_per_query"):
            policy_limits.load_max_metrics_per_query(tmp_path / "defs")
    with pytest.raises(RuntimeError, match="query_policy.yml"):
        policy_limits.load_max_metrics_per_query(tmp_path / "missing")


# ---------------------------------------------------------------- validation (C-2.5 steps 1-6)
def test_combinable_metrics_validate(registry):
    out = validate(registry, metrics=["flight_count", "cancellation_rate"], group_by=[{"dimension": "carrier_code"}])
    assert isinstance(out, ValidatedParts)
    assert [m.name for m in out.metrics] == ["flight_count", "cancellation_rate"]


def test_one_metric_list_equals_old_single_metric_behaviour(registry):
    out = validate(registry, metrics=["cancellation_rate"], group_by=[{"dimension": "carrier_code"}])
    assert isinstance(out, ValidatedParts) and [m.name for m in out.metrics] == ["cancellation_rate"]


def test_unknown_metric_names_first_failure(registry):
    u = _unsupported(validate(registry, metrics=["flight_count", "nope", "nope2"]))
    assert u.reason == "no_approved_metric" and u.conflicting_metrics == []
    assert u.message == "Metric 'nope' is not an approved metric."


def test_draft_metric_not_exposable_in_consumer_view(registry):
    assert registry.metric("diversion_rate") is None
    u = _unsupported(validate(registry, metrics=["flight_count", "diversion_rate"]))
    assert u.reason == "no_approved_metric"


def test_not_combinable_without_combinable_with(registry, monkeypatch):
    # a metric with no combinable_with is never combinable (C-2.5 step 3)
    cat = registry.catalog
    monkeypatch.setitem(cat.metrics, "cancellation_rate",
                        dataclasses.replace(cat.metrics["cancellation_rate"], combinable_with=None))
    u = _unsupported(validate(registry, metrics=["flight_count", "cancellation_rate"]))
    assert u.reason == "metrics_not_combinable"
    assert u.conflicting_metrics == ["flight_count", "cancellation_rate"]
    u = _unsupported(validate(registry, metrics=["cancellation_rate", "flight_count"]))
    assert u.conflicting_metrics == ["cancellation_rate", "flight_count"]


def test_not_combinable_conflicting_list_in_request_order(registry, monkeypatch):
    cat = registry.catalog
    for name in ("cancellation_rate", "arrival_delay_rate"):
        monkeypatch.setitem(cat.metrics, name, dataclasses.replace(cat.metrics[name], combinable_with="other"))
    u = _unsupported(validate(registry, metrics=["flight_count", "cancellation_rate", "cancelled_flight_count",
                                                 "arrival_delay_rate"]))
    assert u.reason == "metrics_not_combinable"
    assert u.conflicting_metrics == ["flight_count", "cancellation_rate", "arrival_delay_rate"]


def test_not_combinable_when_time_basis_differs(registry, monkeypatch):
    cat = registry.catalog
    monkeypatch.setitem(cat.metrics, "cancellation_rate",
                        dataclasses.replace(cat.metrics["cancellation_rate"], time_basis="other_basis"))
    u = _unsupported(validate(registry, metrics=["flight_count", "cancellation_rate"]))
    assert u.reason == "metrics_not_combinable" and u.conflicting_metrics == ["flight_count", "cancellation_rate"]


def test_dimension_allowed_by_all_metrics_via_intersection(registry, monkeypatch):
    cat = registry.catalog
    monkeypatch.setitem(cat.metrics, "cancellation_rate", dataclasses.replace(
        cat.metrics["cancellation_rate"], allowed_dimensions=("flight_date", "carrier_code")))
    assert isinstance(validate(registry, metrics=["flight_count", "cancellation_rate"],
                               group_by=[{"dimension": "carrier_code"}]), ValidatedParts)


@pytest.mark.parametrize("where", ["group_by", "filters"])
def test_dimension_blocked_by_some_metrics(registry, monkeypatch, where):
    cat = registry.catalog
    monkeypatch.setitem(cat.metrics, "cancellation_rate", dataclasses.replace(
        cat.metrics["cancellation_rate"], allowed_dimensions=("flight_date", "carrier_code")))
    arg = ([{"dimension": "origin_state"}] if where == "group_by" else
           [{"dimension": "origin_state", "operator": "equals", "values": ["IL"]}])
    u = _unsupported(validate(registry, metrics=["flight_count", "cancellation_rate"], **{where: arg}))
    assert u.reason == "no_supported_dimension" and u.conflicting_metrics == ["cancellation_rate"]
    assert u.message == "Dimension 'origin_state' is not available for: cancellation_rate."


def test_dimension_blocked_by_all_metrics(registry, monkeypatch):
    cat = registry.catalog
    for name in ("flight_count", "cancellation_rate"):
        monkeypatch.setitem(cat.metrics, name, dataclasses.replace(cat.metrics[name],
                                                                   allowed_dimensions=("flight_date",)))
    u = _unsupported(validate(registry, metrics=["flight_count", "cancellation_rate"],
                              group_by=[{"dimension": "origin_state"}]))
    assert u.reason == "no_supported_dimension" and u.conflicting_metrics == ["flight_count", "cancellation_rate"]
    u1 = _unsupported(validate(registry, metrics=["flight_count"], group_by=[{"dimension": "origin_state"}]))
    assert u1.reason == "no_supported_dimension" and u1.conflicting_metrics == []


def test_unknown_dimension_is_unsupported(registry):
    u = _unsupported(validate(registry, metrics=["flight_count", "cancellation_rate"],
                              group_by=[{"dimension": "no_such_dimension"}]))
    assert u.reason == "no_supported_dimension"


def test_policy_terms_checked_over_all_metrics(registry):
    out = validate(registry, metrics=["flight_count", "passengers"])
    assert _unsupported(out).reason == "insufficient_context"


def test_too_many_group_by_is_unsupported_operation(registry):
    dims = ["carrier_code", "origin_state", "day_of_week", "month", "flight_date", "origin_airport_code"]
    u = _unsupported(validate(registry, metrics=["flight_count", "cancellation_rate"],
                              group_by=[{"dimension": d} for d in dims]))
    assert u.reason == "unsupported_operation"


def test_unsupported_details_always_serialise_conflicting_metrics(registry):
    j = validate(registry, metrics=["nope"]).model_dump()
    assert j["unsupported"]["conflicting_metrics"] == []


# ---------------------------------------------------------------- planner / compiler
def _sql(db):
    return [s for s, _ in db.statements if "flight_count" in s or "cancellation_rate" in s][-1]


def test_sql_selects_metrics_in_request_order_and_orders_by_first_metric(client, body, db):
    db.result = lambda s, p: (["carrier_code", "cancellation_rate", "flight_count"], [("UA", 0.1, 5)])
    r = client.post("/api/v1/query/execute", json=body(metrics=["cancellation_rate", "flight_count"],
                                                       group_by=[{"dimension": "carrier_code"}]))
    assert r.status_code == 200 and r.json()["outcome"] == "result", r.text
    sql = _sql(db)
    assert sql.index('AS "cancellation_rate"') < sql.index('AS "flight_count"')
    assert 'ORDER BY "cancellation_rate" DESC NULLS LAST, "carrier_code" ASC' in sql
    assert "NULLIF" in sql                                    # ratio uses its own denominator (C-4.6)
    params = [p for s, p in db.statements if "flight_count" in s][-1]
    assert params["limit"] == 501                             # effective_limit + 1 (C-4.3)


# ---------------------------------------------------------------- response shape (C-2.5)
def test_multi_metric_response_shape(client, body, db):
    db.result = lambda s, p: (["carrier_code", "flight_count", "cancellation_rate"],
                              [("UA", 10, 0.25), ("AA", 8, None)])
    r = client.post("/api/v1/query/execute", json=body(metrics=["flight_count", "cancellation_rate"],
                                                       group_by=[{"dimension": "carrier_code"}]))
    j = r.json()
    assert r.status_code == 200 and j["outcome"] == "result", r.text
    cols = j["columns"]
    assert [(c["name"], c["role"]) for c in cols] == [("carrier_code", "dimension"), ("flight_count", "metric"),
                                                      ("cancellation_rate", "metric")]
    assert cols[0]["unit"] is None and cols[1]["unit"] == "count" and cols[2]["unit"] in ("ratio", "percent")
    assert [c["data_type"] for c in cols[1:]] == ["integer", "number"]
    assert j["rows"] == [["UA", 10, 0.25], ["AA", 8, None]]                  # aligned with columns (C-4.5)
    assert j["row_count"] == 2 and j["truncated"] is False
    assert list(j["metric_versions"]) == ["flight_count", "cancellation_rate"]
    assert j["metadata"]["metrics"] == ["flight_count", "cancellation_rate"]
    assert j["chart_hints"] == [{"chart_type": "table", "x_field": None, "y_fields": [], "color_field": None,
                                 "title": None, "confidence": 1.0}]
    for gone in ("metric", "metric_label", "metric_version", "row_count", "truncated"):
        assert gone not in j["metadata"]


def test_not_combinable_over_http_is_200_unsupported(settings, executor, tmp_path, body):
    import shutil
    from fastapi.testclient import TestClient
    from semantic_query_service.app.main import create_app
    defs = tmp_path / "defs"
    shutil.copytree(DEFS, defs)
    f = defs / "metrics" / "flight_operations_metrics.yml"
    text = f.read_text()
    head, tail = text.split("  - name: cancellation_rate", 1)
    tail = tail.replace("combinable_with: flight_operations", "combinable_with: other_group", 1)
    f.write_text(head + "  - name: cancellation_rate" + tail)
    settings.semantic_layer_definitions_dir = str(defs)
    c = TestClient(create_app(settings, executor))
    bd = body(metrics=["flight_count", "cancellation_rate"])
    bd["catalog_version"] = c.get("/health/ready").json()["catalog_version"]
    r = c.post("/api/v1/query/execute", json=bd)
    j = r.json()
    assert r.status_code == 200 and j["outcome"] == "unsupported", r.text
    assert j["unsupported"]["reason"] == "metrics_not_combinable"
    assert j["unsupported"]["conflicting_metrics"] == ["flight_count", "cancellation_rate"]


def test_duplicate_metrics_over_http_is_422(client, body):
    r = client.post("/api/v1/query/execute", json=body(metrics=["flight_count", "flight_count"]))
    assert r.status_code == 422 and r.json()["type"].endswith("query-validation-failed")


# ---------------------------------------------------------------- truncation and determinism
def _run(settings, executor, db, body, nrows, limit):
    from fastapi.testclient import TestClient
    from semantic_query_service.app.main import create_app
    db.result = lambda s, p: (["origin_state", "flight_count", "cancellation_rate"],
                              [("S%03d" % i, i, i / 1000) for i in range(nrows)])
    c = TestClient(create_app(settings, executor))
    bd = body(metrics=["flight_count", "cancellation_rate"], group_by=[{"dimension": "origin_state"}],
              requested_limit=limit)
    bd["catalog_version"] = c.get("/health/ready").json()["catalog_version"]
    return c.post("/api/v1/query/execute", json=bd).json()


def test_truncates_instead_of_rejecting(settings, executor, db, body):
    j = _run(settings, executor, db, body, nrows=11, limit=10)       # DB returned effective_limit + 1
    assert j["outcome"] == "result" and j["truncated"] is True
    assert j["row_count"] == 10 == len(j["rows"]) and j["query_execution"]["effective_limit"] == 10
    assert j["rows"][0][0] == "S000"                                  # the first rows are kept


def test_exactly_limit_rows_is_not_truncated(settings, executor, db, body):
    j = _run(settings, executor, db, body, nrows=10, limit=10)
    assert j["truncated"] is False and j["row_count"] == 10


def test_effective_limit_is_min_of_requested_and_policy_max(settings, executor, db, body, internal):
    cap = internal.catalog.query_policy.max_result_rows
    j = _run(settings, executor, db, body, nrows=3, limit=5000)
    assert j["query_execution"]["effective_limit"] == min(5000, cap)


def test_deterministic_same_request_same_response(client, body, db):
    db.result = lambda s, p: (["carrier_code", "flight_count", "cancellation_rate"],
                              [("UA", 10, 0.25), ("AA", 8, 0.1), ("DL", 8, 0.2)])
    req = body(metrics=["flight_count", "cancellation_rate"], group_by=[{"dimension": "carrier_code"}])
    a = client.post("/api/v1/query/execute", json=req).json()
    b = client.post("/api/v1/query/execute", json=req).json()
    for k in ("columns", "rows", "truncated", "row_count", "metric_versions"):
        assert a[k] == b[k]


# ---------------------------------------------------------------- audit (C-3.4)
def test_audit_records_every_metric(client, body, settings):
    client.post("/api/v1/query/execute", json=body(metrics=["flight_count", "cancellation_rate"]))
    a = json.loads(open(settings.query_audit_log_path).read().splitlines()[-1])
    assert a["metrics"] == ["flight_count", "cancellation_rate"]
    assert list(a["metric_versions"]) == ["flight_count", "cancellation_rate"]
    assert "metric" not in a and "metric_version" not in a


def test_audit_before_validation_succeeds(client, body, settings):
    client.post("/api/v1/query/execute", json=body(metrics=["flight_count", "nope"]))
    a = json.loads(open(settings.query_audit_log_path).read().splitlines()[-1])
    assert a["metrics"] == ["flight_count", "nope"] and a["metric_versions"] == {} and a["outcome"] == "unsupported"
    client.post("/api/v1/query/execute", json={"request_id": "r2", "catalog_version": "x",
                                               "semantic_request": {"metric": "flight_count"}})
    a = json.loads(open(settings.query_audit_log_path).read().splitlines()[-1])
    assert a["metrics"] == [] and a["metric_versions"] == {}


def test_read_only_guard_unchanged(client, body, db):
    client.post("/api/v1/query/execute", json=body(metrics=["flight_count", "cancellation_rate"]))
    assert all(s.lstrip().upper().startswith(("SELECT", "WITH")) for s, _ in db.statements)
