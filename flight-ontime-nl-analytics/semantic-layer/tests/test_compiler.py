"""Compiler tests (REQ-010, REQ-011, REQ-022, REQ-026, REQ-033; CONTRACT C-2, C-5, C-6.2).

TC ids refer to artifacts/test-cases.json. All tests are offline: execution uses a fake
connection and the CLI is driven in-process without ``--execute``.
"""
from __future__ import annotations

import json
import logging
import re

import pytest

from semantic_layer.compiler import compile_request, execute, main
from semantic_layer.errors import DatabaseError
from semantic_layer.pg_client import ReadOnlyConnection
from semantic_layer.policy import assert_read_only_sql

ALL_STATUSES = {"ok", "clarification_required", "unsupported", "invalid_request"}


def run(catalog, request, audience="internal", **kw):
    return compile_request(request, catalog, audience=audience, **kw)


# ------------------------------------------------------------------ ok path (TC-045/046)
def test_simple_metric_compiles_parameterised(catalog):
    r = run(catalog, {"metric": "flight_count"})
    assert r.status == "ok" and r.reason_code is None
    assert r.sql == 'SELECT COUNT(*) AS "flight_count"\nFROM "ontime"."flight" AS f\nORDER BY "flight_count" DESC NULLS LAST\nLIMIT %(limit)s'
    assert r.params == {"limit": 101}  # default_limit + 1 (C-2.8.5)
    assert r.metadata["limit"] == 100
    assert r.metadata["definitions_version"] == catalog.definitions_version
    assert r.metadata["audience"] == "internal"
    assert_read_only_sql(r.sql, catalog.query_policy)


def test_compile_is_deterministic(catalog):
    req = {"metric": "cancellation_rate", "dimensions": ["carrier_code", "flight_date__month"],
           "request_id": "fixed-id"}
    a, b = run(catalog, req), run(catalog, req)
    assert a.sql == b.sql and a.params == b.params and a.to_dict() == b.to_dict()


def test_user_values_are_bound_never_interpolated(catalog):
    evil = "'; DROP TABLE ontime.flight;--"
    r = run(catalog, {"metric": "flight_count", "dimensions": ["carrier_code"],
                      "filters": [{"dimension": "carrier_code", "operator": "in", "value": ["AA", evil]}]})
    assert r.status == "ok"
    assert "DROP" not in r.sql and evil not in r.sql and "'" not in r.sql.replace("'month'", "").replace("'week'", "")
    assert evil in json.dumps(r.params)
    assert_read_only_sql(r.sql, catalog.query_policy)


def test_join_only_when_needed_and_fixed_alias_order(catalog):
    base = run(catalog, {"metric": "flight_count"})
    assert "JOIN" not in base.sql
    r = run(catalog, {"metric": "flight_count",
                      "dimensions": ["destination_city", "origin_city", "carrier_code"]})
    assert r.status == "ok"
    pos = [r.sql.index(f"AS {a} ON") for a in ("c", "ao", "ad")]
    assert pos == sorted(pos), "joins are emitted in the order carrier, origin, destination"
    assert r.sql.count("JOIN") == 3


def test_date_filter_between_binds_two_params(catalog):
    r = run(catalog, {"metric": "flight_count", "filters": [
        {"dimension": "flight_date", "operator": "between", "value": ["2026-01-01", "2026-01-31"]}]})
    assert r.status == "ok"
    assert 'WHERE f."fl_date" BETWEEN %(p1)s AND %(p2)s' in r.sql
    # typed values internally; ISO strings once serialised (C-2.2)
    assert r.params["p1"].isoformat() == "2026-01-01" and r.params["p2"].isoformat() == "2026-01-31"
    assert r.to_dict()["params"] == {"p1": "2026-01-01", "p2": "2026-01-31", "limit": 101}


def test_order_by_and_limit_clamp(catalog):
    r = run(catalog, {"metric": "flight_count", "dimensions": ["carrier_code"], "limit": 5000,
                      "order_by": [{"field": "flight_count", "direction": "desc"}]})
    assert r.status == "ok"
    assert r.params["limit"] == 1001 and r.metadata["limit"] == 1000
    assert "limit_clamped" in r.metadata["warnings"]
    assert 'ORDER BY "flight_count" DESC' in r.sql


def test_multiple_metrics_in_request_order(catalog):
    r = run(catalog, {"metrics": ["flight_count", "cancelled_flight_count"], "dimensions": ["quarter"]})
    assert r.status == "ok"
    assert r.sql.index('"flight_count"') < r.sql.index('"cancelled_flight_count"')


def test_ratio_metric_uses_nullif_denominator(catalog):
    r = run(catalog, {"metric": "cancellation_rate"})
    assert r.status == "ok" and "NULLIF(" in r.sql and "::numeric" in r.sql


# ------------------------------------------------------------------ invalid_request
@pytest.mark.parametrize("request_,code", [
    ({}, "schema_error"),
    ({"bad_key": 1, "metric": "flight_count"}, "schema_error"),
    ({"metrics": ["flight_count", "flight_count"]}, "schema_error"),
    ({"metric": "flight_count", "request_id": "bad id!"}, "schema_error"),
    ({"metric": "flight_count", "limit": "x"}, "schema_error"),
    ({"metric": "flight_count", "limit": 0}, "invalid_limit"),
    ({"metric": "flight_count", "dimensions": ["flight_date__year"]}, "grain_not_allowed"),
    ({"metric": "flight_count", "dimensions": ["carrier_code"], "order_by": [{"field": "zzz"}]},
     "order_by_field_not_selected"),
    ({"metric": "flight_count", "filters": [{"dimension": "carrier_code", "operator": "gt", "value": "A"}]},
     "operator_not_allowed"),
    ({"metric": "flight_count", "filters": [{"dimension": "flight_date", "operator": "eq", "value": "bad"}]},
     "invalid_filter_value"),
])
def test_invalid_requests_rejected(catalog, request_, code):
    r = run(catalog, request_)
    assert r.status == "invalid_request" and r.reason_code == code
    assert r.sql is None and r.params is None


def test_non_dict_request_is_invalid_not_an_exception(catalog):
    r = run(catalog, "flight_count")
    assert r.status == "invalid_request" and r.reason_code == "schema_error"


def test_too_many_dimensions_rejected(catalog):
    dims = ["flight_date", "day_of_week", "month", "quarter", "carrier_code", "origin_city"]
    r = run(catalog, {"metric": "flight_count", "dimensions": dims})
    assert r.status == "invalid_request"


def test_unknown_audience_is_a_programming_error(catalog):
    with pytest.raises(ValueError):
        run(catalog, {"metric": "flight_count"}, audience="public")


# ------------------------------------------------------------------ clarification paths (REQ-022, Q1, Q4)
@pytest.mark.parametrize("term", ["delay_rate", "delayed_flights", "Delay Rate", "late_rate"])
def test_q1_delay_direction_needs_clarification(catalog, term):  # TC-043/044
    r = run(catalog, {"metric": term})
    assert r.status == "clarification_required" and r.reason_code == "delay_direction_unspecified"
    assert r.options == ["departure_delay_rate", "arrival_delay_rate",
                         "departure_delayed_flight_count", "arrival_delayed_flight_count"]
    assert r.sql is None


@pytest.mark.parametrize("term", ["airport", "airport_code", "city", "state"])
def test_q4_airport_role_needs_clarification_as_dimension(catalog, term):
    r = run(catalog, {"metric": "flight_count", "dimensions": [term]})
    assert r.status == "clarification_required" and r.reason_code == "airport_role_unspecified"
    assert r.options == ["origin_airport_code", "destination_airport_code", "origin_city",
                         "destination_city", "origin_state", "destination_state"]


def test_q4_airport_role_needs_clarification_as_metric_term(catalog):
    r = run(catalog, {"metric": "airport_code"})
    assert r.status == "clarification_required" and r.reason_code == "airport_role_unspecified"


def test_q4_explicit_roles_compile(catalog):
    o = run(catalog, {"metric": "flight_count", "dimensions": ["origin_city"]})
    d = run(catalog, {"metric": "flight_count", "dimensions": ["destination_city"]})
    assert o.status == d.status == "ok"
    assert 'ao."airport_id" = f."origin_airport_id"' in o.sql
    assert 'ad."airport_id" = f."dest_airport_id"' in d.sql


# ------------------------------------------------------------------ unsupported paths (REQ-026)
@pytest.mark.parametrize("term", ["average_delay_minutes", "passengers", "revenue", "tail_number"])
def test_unavailable_data_is_unsupported_not_guessed(catalog, term):  # TC-041/042
    r = run(catalog, {"metric": term})
    assert r.status == "unsupported" and r.reason_code == "refers_to_unavailable_data"
    assert r.sql is None


def test_unknown_metric_unsupported(catalog):
    r = run(catalog, {"metric": "not_a_metric"})
    assert r.status == "unsupported" and r.reason_code == "metric_not_in_catalog"


def test_unknown_dimension_unsupported(catalog):
    r = run(catalog, {"metric": "flight_count", "dimensions": ["carrier"]})
    assert r.status == "unsupported" and r.reason_code == "dimension_not_in_catalog"


def test_dimension_not_allowed_for_metric(catalog):
    r = run(catalog, {"metric": "cancellation_rate", "dimensions": ["cancellation_reason"]})
    assert r.status == "unsupported"
    assert r.reason_code in ("dimension_not_allowed_for_metric", "dimension_not_exposable")


@pytest.mark.parametrize("audience,expected", [
    ("consumer", "metric_not_exposable"),
    ("internal", "metric_not_exposable"),
    ("reconciliation", None),
])
def test_q2_draft_metric_only_for_reconciliation(catalog, audience, expected):  # TC-040
    assert catalog.metrics["on_time_arrival_rate"].status == "draft"
    r = run(catalog, {"metric": "on_time_arrival_rate"}, audience=audience)
    if expected:
        assert r.status == "unsupported" and r.reason_code == expected and r.sql is None
    else:
        assert r.status == "ok" and "arr_del15" in r.sql


def test_audience_minimum_status_consumer_needs_approved(catalog):
    """Nothing is approved yet (pending sign-off), so a consumer cannot get any metric."""
    assert not any(m.status == "approved" for m in catalog.metrics.values())
    r = run(catalog, {"metric": "flight_count"}, audience="consumer")
    assert r.status == "unsupported" and r.reason_code == "metric_not_exposable"
    assert run(catalog, {"metric": "flight_count"}, audience="internal").status == "ok"


def test_q3_departure_delay_rate_carries_open_question_warning(catalog):
    r = run(catalog, {"metric": "departure_delay_rate"})
    assert r.status == "ok" and "open_question:Q3" in r.metadata["warnings"]
    assert 'SUM(f."dep_del15")' in r.sql and 'NULLIF(COUNT(f."dep_del15"), 0)' in r.sql


def test_q5_carrier_display_names_not_guessed(catalog):
    for term in ("carrier_name", "airline_name"):
        r = run(catalog, {"metric": term})
        assert r.status == "unsupported" and r.reason_code == "mapping_not_approved"
    mapping = catalog.mappings["carrier_display_names"]
    assert mapping.status == "draft" and not mapping.values


def test_q6_draft_group_dimensions_not_exposed(catalog):
    for dim in ("distance_group", "departure_delay_group", "arrival_delay_group"):
        assert catalog_dimension_status(catalog, dim) == "draft"
        r = run(catalog, {"metric": "flight_count", "dimensions": [dim]})
        assert r.status == "unsupported" and r.reason_code == "dimension_not_exposable"
        assert run(catalog, {"metric": "flight_count", "dimensions": [dim]}, audience="reconciliation").status == "ok"


def test_q7_month_is_calendar_month_of_fl_date(catalog):
    r = run(catalog, {"metric": "flight_count", "dimensions": ["flight_date__month"]})
    assert r.status == "ok"
    assert "DATE_TRUNC('month', f.\"fl_date\")" in r.sql
    w = run(catalog, {"metric": "flight_count", "dimensions": ["flight_date__week"]})
    assert "DATE_TRUNC('week', f.\"fl_date\")" in w.sql


def test_constant_dimension_warnings(catalog):
    q = run(catalog, {"metric": "flight_count", "dimensions": ["quarter"]})
    m = run(catalog, {"metric": "flight_count", "dimensions": ["month"]})
    assert "constant_dimension:quarter" in q.metadata["warnings"]
    assert "constant_dimension:month" in m.metadata["warnings"]


def test_destination_code_uses_city_fallback(catalog):
    r = run(catalog, {"metric": "flight_count", "dimensions": ["destination_airport_code"]})
    assert r.status == "ok"
    assert 'COALESCE(ad."code"::text, ad."city_name")' in r.sql
    assert "destination_code_city_fallback" in r.metadata["warnings"]


def catalog_dimension_status(catalog, name):
    model = next(iter(catalog.models.values()))
    return next(d.status for d in model.dimensions if d.name == name)


# ------------------------------------------------------------------ to_dict / audit (REQ-033, TC-049)
def test_to_dict_is_json_serialisable(catalog):
    for req in ({"metric": "flight_count"}, {"metric": "delay_rate"}, {"metric": "passengers"}, {}):
        d = run(catalog, req).to_dict()
        assert set(d) == {"status", "reason_code", "message", "options", "sql", "params", "metadata"}
        assert d["status"] in ALL_STATUSES
        json.dumps(d)


class ListHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(json.loads(record.getMessage()))


@pytest.fixture
def audit():
    logger = logging.getLogger("test.audit.%d" % id(object()))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    h = ListHandler()
    logger.addHandler(h)
    return logger, h.records


@pytest.mark.parametrize("req,status", [
    ({"metric": "flight_count", "request_id": "r-1"}, "ok"),
    ({"metric": "delay_rate", "request_id": "r-1"}, "clarification_required"),
    ({"metric": "passengers", "request_id": "r-1"}, "unsupported"),
    ({"bogus": 1, "request_id": "r-1"}, "invalid_request"),
])
def test_every_compile_emits_one_audit_record(catalog, audit, req, status):
    logger, records = audit
    run(catalog, req, logger=logger)
    assert len(records) == 1
    rec = records[0]
    assert rec["event"] == "compile" and rec["status"] == status
    assert rec["request_id"] == "r-1" and rec["definitions_version"] == catalog.definitions_version
    assert rec["audience"] == "internal"
    assert "sql" not in rec and "params" not in rec  # no row data or bound values


def test_audit_record_names_metric_versions(catalog, audit):
    logger, records = audit
    run(catalog, {"metric": "flight_count"}, logger=logger)
    assert records[0]["metric_versions"] == {"flight_count": catalog.metrics["flight_count"].version}


# ------------------------------------------------------------------ execute with a fake connection
def test_execute_returns_dict_rows_and_audits(catalog, audit, fake_connection):
    logger, records = audit
    resp = run(catalog, {"metric": "flight_count"}, logger=logger)
    fc = fake_connection(lambda sql, params: (["flight_count"], [(544003,)]))
    res = execute(resp, ReadOnlyConnection(fc), logger=logger)
    assert res.columns == ["flight_count"] and res.rows == [{"flight_count": 544003}] and res.row_count == 1
    assert fc.executed[0][1] == {"limit": 101}
    assert [r["event"] for r in records] == ["compile", "execute"]
    assert records[1]["row_count"] == 1 and "544003" not in json.dumps(records[1])


def test_execute_refuses_non_ok_responses(catalog, fake_connection):
    resp = run(catalog, {"metric": "delay_rate"})
    fc = fake_connection()
    with pytest.raises(ValueError):
        execute(resp, ReadOnlyConnection(fc))
    assert fc.executed == []


def test_execute_failure_is_audited_and_reraised(catalog, audit, fake_connection):
    logger, records = audit
    resp = run(catalog, {"metric": "flight_count"}, logger=logger)
    fc = fake_connection(fail_with=RuntimeError("boom"))
    with pytest.raises(DatabaseError):
        execute(resp, ReadOnlyConnection(fc), logger=logger)
    assert records[-1]["status"] == "error" and records[-1]["reason_code"] == "execution_failed"


# ------------------------------------------------------------------ CLI (C-2.5 exit codes)
def cli(capsys, tmp_path, request_obj, *extra, definitions=None):
    path = tmp_path / "req.json"
    path.write_text(json.dumps(request_obj) if not isinstance(request_obj, str) else request_obj)
    args = ["--request", str(path)]
    if definitions is not None:
        args += ["--definitions", str(definitions)]
    code = main(args + list(extra))
    return code, capsys.readouterr()


def test_cli_ok_exit_0(capsys, tmp_path, definitions_dir):
    code, out = cli(capsys, tmp_path, {"metric": "flight_count"}, definitions=definitions_dir)
    assert code == 0
    assert json.loads(out.out)["status"] == "ok"


def test_cli_non_ok_exit_1(capsys, tmp_path, definitions_dir):
    code, out = cli(capsys, tmp_path, {"metric": "delay_rate"}, definitions=definitions_dir)
    assert code == 1
    assert json.loads(out.out)["status"] == "clarification_required"


def test_cli_audience_flag_consumer_and_choices(capsys, tmp_path, definitions_dir):
    code, out = cli(capsys, tmp_path, {"metric": "flight_count"}, "--audience", "consumer",
                    definitions=definitions_dir)
    assert code == 1 and json.loads(out.out)["reason_code"] == "metric_not_exposable"
    # CONTRACT C-2.5: the CLI offers internal|consumer only; reconciliation is a library audience.
    code, _ = cli(capsys, tmp_path, {"metric": "flight_count"}, "--audience", "reconciliation",
                  definitions=definitions_dir)
    assert code == 2


def test_cli_unreadable_or_bad_json_exit_2(capsys, tmp_path, definitions_dir):
    code, _ = cli(capsys, tmp_path, "{not json", definitions=definitions_dir)
    assert code == 2
    assert main(["--request", str(tmp_path / "missing.json"), "--definitions", str(definitions_dir)]) == 2


def test_cli_bad_definitions_exit_2(capsys, tmp_path):
    code, _ = cli(capsys, tmp_path, {"metric": "flight_count"}, definitions=tmp_path / "nope")
    assert code == 2


def test_cli_execute_without_credentials_is_a_config_error(capsys, tmp_path, definitions_dir, monkeypatch):
    for var in ("TRANSPORT_STATS_PG_USER", "TRANSPORT_STATS_PG_PASSWORD", "PGUSER", "PGPASSWORD"):
        monkeypatch.delenv(var, raising=False)
    code, out = cli(capsys, tmp_path, {"metric": "flight_count"}, "--execute", "--connection", "env",
                    definitions=definitions_dir)
    assert code == 3
    assert "TRANSPORT_STATS_PG_PASSWORD" in out.err


# ------------------------------------------------------------------ multi-metric / multi-dimension (REQ-024 to REQ-029, C-2.8)
SHAPES = {
    "a": {"metrics": ["flight_count", "cancellation_rate"], "dimensions": ["carrier_code"]},
    "b": {"metrics": ["cancelled_flight_count"], "dimensions": ["carrier_code", "origin_airport_code"]},
    "c": {"metrics": ["flight_count", "cancelled_flight_count"], "dimensions": ["carrier_code", "origin_airport_code"],
          "filters": [{"dimension": "origin_airport_code", "operator": "in", "value": ["ORD", "MDW"]}]},
}


@pytest.mark.parametrize("key", sorted(SHAPES))
def test_multi_metric_shapes(catalog, key):
    req = SHAPES[key]
    r = run(catalog, req)
    assert r.status == "ok", r.message
    select = r.sql.split("\nFROM")[0]
    for m in req["metrics"]:
        assert select.count(f'AS "{m}"') == 1
    assert select.index(req["dimensions"][0]) < select.index(f'AS "{req["metrics"][0]}"')
    group = r.sql.split("GROUP BY ")[1].split("\nORDER BY")[0]
    assert len(group.split(", ")) == len(req["dimensions"])
    order = r.sql.split("ORDER BY ")[1].split("\nLIMIT")[0].split(", ")
    assert order[0] == f'"{req["metrics"][0]}" DESC NULLS LAST'
    assert order[1:] == [f'"{d}" ASC' for d in req["dimensions"]]
    assert r.sql.endswith("LIMIT %(limit)s") and r.params["limit"] == 101
    assert "'ORD'" not in r.sql and "'MDW'" not in r.sql  # user values only as params
    if "filters" in req:
        assert r.params["p1"] == ["ORD", "MDW"] and "%(p1)s" in r.sql
    assert [m["name"] for m in r.metadata["metrics"]] == req["metrics"]
    assert all(m["label"] for m in r.metadata["metrics"])


def test_ratio_metric_uses_own_numerator_and_denominator(catalog):
    r = run(catalog, SHAPES["a"])
    assert "NULLIF" in r.sql and '"cancellation_rate"' in r.sql


def test_explicit_order_by_comes_first_and_metric_not_repeated(catalog):
    r = run(catalog, {**SHAPES["a"], "order_by": [{"field": "flight_count", "direction": "asc"}]})
    order = r.sql.split("ORDER BY ")[1].split("\nLIMIT")[0]
    assert order == '"flight_count" ASC, "carrier_code" ASC'


def test_repeated_compiles_are_identical(catalog):
    first = run(catalog, SHAPES["c"])
    assert all(run(catalog, SHAPES["c"]).sql == first.sql for _ in range(3))
    assert run(catalog, SHAPES["c"]).params == first.params


def test_max_metrics_comes_from_policy(catalog):
    from dataclasses import replace
    import semantic_layer.compiler as comp
    assert not hasattr(comp, "MAX_METRICS")
    n = catalog.query_policy.max_metrics_per_query
    assert n == 5
    names = list(catalog.metrics)[: n + 1]
    r = run(catalog, {"metrics": names})
    assert r.status == "invalid_request" and r.reason_code == "schema_error"
    assert r.message == f"'metrics' must be a list of 1..{n} strings"
    small = replace(catalog, query_policy=replace(catalog.query_policy, max_metrics_per_query=1))
    assert run(small, {"metrics": ["flight_count", "cancelled_flight_count"]}).status == "invalid_request"


def test_not_combinable_metrics_are_unsupported(catalog):
    from dataclasses import replace
    metrics = dict(catalog.metrics)
    metrics["cancellation_rate"] = replace(metrics["cancellation_rate"], combinable_with=None)
    cat = replace(catalog, metrics=metrics)
    r = run(cat, {"metrics": ["flight_count", "cancellation_rate"]})
    assert r.status == "unsupported" and r.reason_code == "metrics_not_combinable"
    assert r.message == "metrics cannot be combined: flight_count, cancellation_rate"
    assert run(cat, {"metrics": ["cancellation_rate"]}).status == "ok"  # single metric is always fine


def test_execute_truncates_and_flags(catalog, audit, fake_connection):
    logger, records = audit
    resp = run(catalog, {"metrics": ["flight_count"], "dimensions": ["carrier_code"], "limit": 2}, logger=logger)
    assert resp.params["limit"] == 3 and resp.metadata["limit"] == 2
    fc = fake_connection(lambda sql, params: (["carrier_code", "flight_count"], [("AA", 3), ("DL", 2), ("UA", 1)]))
    res = execute(resp, ReadOnlyConnection(fc), logger=logger)
    assert res.truncated is True and res.row_count == 2 and len(res.rows) == 2
    assert records[-1]["row_count"] == 2
    fc2 = fake_connection(lambda sql, params: (["carrier_code", "flight_count"], [("AA", 3), ("DL", 2)]))
    assert execute(resp, ReadOnlyConnection(fc2), logger=logger).truncated is False


def test_audit_lists_every_metric(catalog, audit):
    logger, records = audit
    run(catalog, SHAPES["c"], logger=logger)
    assert records[0]["metrics"] == ["flight_count", "cancelled_flight_count"]
    assert set(records[0]["metric_versions"]) == {"flight_count", "cancelled_flight_count"}
