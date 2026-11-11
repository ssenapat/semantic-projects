"""Live, READ-ONLY checks against the ``tran-stats`` database (REQ-024; CONTRACT C-4, C-6.3).

Skipped (see conftest) unless TRANSPORT_STATS_PG_PASSWORD or PGPASSWORD is set. Every statement
goes through ``ReadOnlyConnection`` (SELECT/WITH only, session ``default_transaction_read_only=on``,
rollback after each fetch). Nothing is written, no DDL is run, and no password is stored here.
"""
from __future__ import annotations

import pytest

from semantic_layer import validator as v
from semantic_layer.compiler import compile_request, execute
from semantic_layer.errors import PolicyViolation
from semantic_layer.pg_client import ConnectionSettings, connect

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def conn(catalog):
    pytest.importorskip("psycopg", reason="psycopg is not installed; install semantic-layer dependencies")
    qp = catalog.query_policy
    settings = ConnectionSettings.from_env(catalog.connection.database)
    with connect(settings, qp.statement_timeout_seconds, qp.idle_in_transaction_session_timeout_seconds) as c:
        yield c


def test_session_is_read_only_with_timeouts(conn):
    r = v.run_session_check(conn)
    assert r.status == "pass", r.details
    s = conn.session_settings()
    assert s["default_transaction_read_only"] == "on"
    assert s["transaction_read_only"] == "on"


def test_write_is_refused_before_reaching_the_database(conn):
    with pytest.raises(PolicyViolation):
        conn.fetch("DELETE FROM ontime.flight")


def test_live_schema_matches_definitions(conn, catalog):
    r = v.run_live_schema(conn, catalog)
    assert r.status in ("pass", "warn"), (r.details, r.offenders)


def test_validation_sql_blocks_hold(conn, validation_dir):
    results = v.run_validation_sql(conn, validation_dir)
    assert results
    failing = [(r.name, r.details) for r in results if r.status == "fail"]
    assert failing == [], failing


def test_metrics_reconcile_with_baselines(conn, catalog):
    results = v.run_reconcile(conn, catalog)
    assert results
    failing = [(r.name, r.details) for r in results if r.status == "fail"]
    assert failing == [], failing


def test_compiled_flight_count_matches_baseline(conn, catalog):
    resp = compile_request({"metric": "flight_count"}, catalog, audience="internal")
    assert resp.status == "ok"
    res = execute(resp, conn)
    assert res.rows[0]["flight_count"] == catalog.metrics["flight_count"].reconciliation.baseline


def test_compiled_grouped_query_respects_limit(conn, catalog):
    resp = compile_request({"metric": "flight_count", "dimensions": ["carrier_code"], "limit": 3},
                           catalog, audience="internal")
    assert resp.status == "ok"
    res = execute(resp, conn)
    assert 1 <= res.row_count <= 3


def _totals(catalog, conn, request):
    resp = compile_request(request, catalog, audience="internal")
    assert resp.status == "ok", resp.message
    res = execute(resp, conn)
    assert not res.truncated
    return res


def test_grouped_multi_metric_totals_match_baseline(catalog, conn):
    """Grouped totals equal validation/metric_reconciliation.sql#baseline_totals (REQ-031, REQ-043, REQ-044)."""
    res = _totals(catalog, conn, {"metrics": ["flight_count", "cancelled_flight_count"],
                                  "dimensions": ["carrier_code"], "limit": 1000})
    assert sum(r["flight_count"] for r in res.rows) == 544003
    assert sum(r["cancelled_flight_count"] for r in res.rows) == 25635


def test_multi_metric_values_equal_single_metric_values(catalog, conn):
    multi = _totals(catalog, conn, {"metrics": ["flight_count", "cancelled_flight_count"],
                                    "dimensions": ["carrier_code"], "limit": 1000})
    for name in ("flight_count", "cancelled_flight_count"):
        single = _totals(catalog, conn, {"metrics": [name], "dimensions": ["carrier_code"], "limit": 1000})
        assert {r["carrier_code"]: r[name] for r in single.rows} == {r["carrier_code"]: r[name] for r in multi.rows}
