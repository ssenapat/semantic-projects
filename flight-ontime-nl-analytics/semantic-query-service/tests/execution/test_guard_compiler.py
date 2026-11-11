import pytest

from semantic_query_service.app.schemas.errors import SqlGuardRejection
from semantic_query_service.app.services import sql_guard, sql_compiler

OK = "SELECT COUNT(*) AS n FROM ontime.flight AS f LIMIT 10"


def test_guard_accepts_select_and_placeholders():
    sql_guard.check(OK)
    sql_guard.check("SELECT f.carrier AS c FROM ontime.flight AS f WHERE f.fl_date >= %(p1)s LIMIT 5")
    sql_guard.check("WITH x AS (SELECT 1 AS a FROM ontime.flight LIMIT 1) SELECT a FROM x LIMIT 1")


@pytest.mark.parametrize("sql", [
    "DELETE FROM ontime.flight",
    "INSERT INTO ontime.flight VALUES (1)",
    "UPDATE ontime.flight SET a = 1",
    "DROP TABLE ontime.flight",
    "CREATE TABLE t AS SELECT 1",
    "TRUNCATE ontime.flight",
    "SELECT 1 FROM ontime.flight LIMIT 1; DROP TABLE x",
    "SELECT * FROM ontime.flight",                      # no LIMIT
    "SELECT 1 FROM pg_catalog.pg_tables LIMIT 1",
    "SELECT 1 FROM public.secret LIMIT 1",
    "SELECT pg_sleep(1) FROM ontime.flight LIMIT 1",
    "SELECT lo_import('/etc/passwd') FROM ontime.flight LIMIT 1",
    "SELECT 1 INTO t FROM ontime.flight LIMIT 1",
    "WITH d AS (DELETE FROM ontime.flight RETURNING 1) SELECT 1 FROM d LIMIT 1",
    "COPY ontime.flight TO '/tmp/x'",
    "",
])
def test_guard_rejects(sql):
    with pytest.raises(SqlGuardRejection) as e:
        sql_guard.check(sql)
    assert "ontime" not in str(e.value)               # never echoes SQL


@pytest.fixture
def internal(settings):
    from semantic_query_service.app.services import internal_catalog_loader
    return internal_catalog_loader.load(settings.semantic_layer_definitions_dir)


def _plan(**kw):
    from semantic_query_service.app.schemas.query_plan import ValidatedQueryPlan, ResolvedDateRange
    base = dict(request_id="r1", catalog_version="v", metrics=["flight_count"], metric_versions={"flight_count": 1},
                source_model="m", group_by=[], resolved_filters=[], mandatory_filters=[], requested_limit=500)
    base.update(kw)
    if base.get("resolved_date_range"):
        base["resolved_date_range"] = ResolvedDateRange(**base["resolved_date_range"])
    return ValidatedQueryPlan(**base)


def test_compile_uses_sp1_and_passes_guard(internal):
    plan = _plan(group_by=[{"dimension": "flight_date", "time_grain": "month", "sp1_term": "flight_date__month"}],
                 resolved_date_range={"start_inclusive": "2026-01-01", "end_exclusive": "2026-02-01",
                                      "time_basis": "flight_date"}, requested_limit=5000)
    cq = sql_compiler.compile_plan(plan, internal, "internal")
    assert hasattr(cq, "sql"), cq
    assert cq.effective_limit == internal.catalog.query_policy.max_result_rows
    assert cq.params
    sql_guard.check(cq.sql, internal.catalog.query_policy)


def test_compile_quarter_grain_unsupported(internal):
    plan = _plan(group_by=[{"dimension": "flight_date", "time_grain": "quarter", "sp1_term": "flight_date__quarter"}])
    out = sql_compiler.compile_plan(plan, internal, "internal")
    assert out.outcome == "unsupported" and out.unsupported.reason == "unsupported_operation"


def test_compile_unknown_metric_unsupported(internal):
    out = sql_compiler.compile_plan(_plan(metrics=["nope"]), internal, "internal")
    assert out.outcome == "unsupported" and out.unsupported.reason == "no_approved_metric"
