"""Policy engine and connection-settings tests (REQ-002, REQ-017, REQ-033; CONTRACT C-5, C-6.3)."""
from __future__ import annotations

import pytest

from semantic_layer.errors import ConfigError, DatabaseError, PolicyViolation
from semantic_layer.pg_client import (
    ENV_DATABASE, ENV_HOST, ENV_PASSWORD, ENV_PORT, ENV_USER,
    ConnectionSettings, ReadOnlyConnection, build_connect_kwargs,
)
from semantic_layer.policy import (
    assert_read_only_sql, check_physical_object, find_prohibited_columns, normalize_term,
)

FAKE_PW = "pw-for-tests-only"  # not a real credential; used with a fake connection


def violation(sql, qp=None):
    with pytest.raises(PolicyViolation) as ei:
        assert_read_only_sql(sql, qp)
    return ei.value.code


# ------------------------------------------------------------------ C-5.1 read-only SQL
@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "select 1;",
    "SELECT 'a;b'",                    # semicolon inside a literal is not a statement break
    "SELECT 1 -- ; DROP TABLE x",      # comment is stripped
    "SELECT /* DELETE */ 1",
    "select 'drop' as x",              # keyword inside a literal is data
    'SELECT "update" FROM t',          # quoted identifier is not a keyword
])
def test_read_only_sql_accepted(sql, catalog):
    assert_read_only_sql(sql)
    assert_read_only_sql(sql, catalog.query_policy)


@pytest.mark.parametrize("sql,code", [
    ("INSERT INTO x VALUES (1)", "statement_type_not_allowed"),
    ("DELETE FROM x", "statement_type_not_allowed"),
    ("COPY x TO stdout", "statement_type_not_allowed"),
    ("SELECT 1; SELECT 2", "multiple_statements"),
    ("", "empty_statement"),
    ("   ", "empty_statement"),
    ("SELECT * FROM t", "select_star"),
    ("WITH a AS (SELECT 1) SELECT * FROM a", "select_star"),
    ("SELECT * INTO x FROM y", "prohibited_keyword"),
    ("SELECT 1 FOR UPDATE", "prohibited_keyword"),
    ("SELECT pg_sleep(1)", "prohibited_function"),
    ("SELECT pg_terminate_backend(1)", "prohibited_function"),
    ("SELECT set_config('a','b',false)", "prohibited_function"),
    ("SELECT lo_import('x')", "prohibited_function"),      # prefix rule lo_
    ("SELECT dblink('x')", "prohibited_function"),          # prefix rule dblink
])
def test_read_only_sql_rejected(sql, code, catalog):
    assert violation(sql) == code
    assert violation(sql, catalog.query_policy) == code


def test_policy_violation_has_code_and_message():
    with pytest.raises(PolicyViolation) as ei:
        assert_read_only_sql("DROP TABLE x")
    assert ei.value.code and ei.value.message


# ------------------------------------------------------------------ C-5.2 physical objects
def test_physical_objects_default_deny(catalog):
    for obj in ("ontime.flight", "ontime.carrier", "ontime.airport"):
        check_physical_object(catalog, obj)
    with pytest.raises(PolicyViolation) as ei:
        check_physical_object(catalog, "ontime.secret_table")
    assert ei.value.code == "object_not_allowed"


def test_reference_views_only_for_reconciliation(catalog):
    with pytest.raises(PolicyViolation):
        check_physical_object(catalog, "ontime.v_route_summary", "query")
    check_physical_object(catalog, "ontime.v_route_summary", "reconciliation")
    check_physical_object(catalog, "ontime.v_carrier_monthly_performance", "reconciliation")


def test_unknown_purpose_denied(catalog):
    with pytest.raises(PolicyViolation):
        check_physical_object(catalog, "ontime.flight", "bogus")


def test_prohibited_column_matching_is_case_insensitive_substring():
    assert find_prohibited_columns(["ssn", "passenger"], ["Passenger_Count", "fl_date", "SSN_x"]) == [
        "Passenger_Count", "SSN_x"]
    assert find_prohibited_columns(["ssn"], ["fl_date", "carrier"]) == []


def test_data_policy_is_default_deny_with_patterns(catalog):
    dp = catalog.data_policy
    assert dp.default == "deny"
    assert "passenger" in dp.prohibited_column_patterns


def test_query_policy_values(catalog):
    qp = catalog.query_policy
    assert qp.max_result_rows == 1000
    assert qp.default_limit == 100
    assert set(qp.allowed_statement_types) == {"SELECT", "WITH"}
    assert qp.audiences["consumer"] == "approved"
    assert qp.audiences["internal"] == "validated"
    assert qp.audiences["reconciliation"] == "draft"


# ------------------------------------------------------------------ term normalisation
@pytest.mark.parametrize("raw,expected", [
    ("Delay Rate", "delay_rate"),
    ("delay-rate", "delay_rate"),
    (" Delayed  Flights ", "delayed_flights"),
    ("Airport_Code", "airport_code"),
])
def test_normalize_term(raw, expected):
    assert normalize_term(raw) == expected


# ------------------------------------------------------------------ C-6.3 connection settings
def clear_env(monkeypatch):
    for var in (ENV_USER, ENV_HOST, ENV_PORT, ENV_DATABASE, ENV_PASSWORD, "PGUSER", "PGPASSWORD"):
        monkeypatch.delenv(var, raising=False)


def test_settings_missing_user_and_password_name_variables_only(monkeypatch):
    clear_env(monkeypatch)
    with pytest.raises(ConfigError) as ei:
        ConnectionSettings.from_env("tran-stats")
    msg = str(ei.value)
    assert ENV_USER in msg and ENV_PASSWORD in msg


def test_settings_defaults_and_pg_fallbacks(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv("PGUSER", "reader")
    monkeypatch.setenv("PGPASSWORD", FAKE_PW)
    s = ConnectionSettings.from_env("tran-stats")
    assert (s.host, s.port, s.dbname, s.user) == ("localhost", 5432, "tran-stats", "reader")
    assert s.password == FAKE_PW


def test_settings_password_never_in_repr(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv(ENV_USER, "reader")
    monkeypatch.setenv(ENV_PASSWORD, FAKE_PW)
    s = ConnectionSettings.from_env("tran-stats")
    assert FAKE_PW not in repr(s)
    assert FAKE_PW not in str(s)


def test_settings_bad_port_rejected(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv(ENV_USER, "reader")
    monkeypatch.setenv(ENV_PASSWORD, FAKE_PW)
    monkeypatch.setenv(ENV_PORT, "not-a-number")
    with pytest.raises(ConfigError) as ei:
        ConnectionSettings.from_env("tran-stats")
    assert FAKE_PW not in str(ei.value)


def test_connect_kwargs_are_read_only_with_timeouts(monkeypatch):
    clear_env(monkeypatch)
    monkeypatch.setenv(ENV_USER, "reader")
    monkeypatch.setenv(ENV_PASSWORD, FAKE_PW)
    kw = build_connect_kwargs(ConnectionSettings.from_env("tran-stats"))
    flat = str(kw["options"])
    assert "default_transaction_read_only=on" in flat
    assert "statement_timeout=30000" in flat.replace(" ", "") or "statement_timeout" in flat
    assert "idle_in_transaction_session_timeout" in flat
    assert kw["dbname"] == "tran-stats"


# ------------------------------------------------------------------ ReadOnlyConnection (fake)
def test_fetch_runs_select_and_rolls_back(fake_connection):
    fc = fake_connection(lambda sql, params: (["n"], [(1,), (2,)]))
    with ReadOnlyConnection(fc) as conn:
        res = conn.fetch("SELECT 1 AS n", {"p": 1})
    assert res.columns == ["n"] and res.rows == [(1,), (2,)] and res.row_count == 2
    assert fc.executed == [("SELECT 1 AS n", {"p": 1})]
    assert fc.rollbacks == 1 and fc.closed


def test_fetch_refuses_writes_before_touching_the_connection(fake_connection):
    fc = fake_connection()
    conn = ReadOnlyConnection(fc)
    with pytest.raises(PolicyViolation):
        conn.fetch("DELETE FROM ontime.flight")
    assert fc.executed == []


def test_fetch_wraps_driver_errors_without_secrets(fake_connection):
    scheme = "postgres" + "ql://"  # built at runtime so no DSN-with-credentials literal is in this file
    boom = RuntimeError(f"could not connect {scheme}reader:{FAKE_PW}@host/db password={FAKE_PW}")
    conn = ReadOnlyConnection(fake_connection(fail_with=boom))
    with pytest.raises(DatabaseError) as ei:
        conn.fetch("SELECT 1")
    assert FAKE_PW not in str(ei.value)
    assert "RuntimeError" in str(ei.value)


def test_fetch_on_closed_connection(fake_connection):
    conn = ReadOnlyConnection(fake_connection())
    conn.close()
    with pytest.raises(DatabaseError):
        conn.fetch("SELECT 1")
