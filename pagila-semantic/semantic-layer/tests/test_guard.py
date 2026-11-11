import pytest

from semantic_layer.errors import GuardError
from semantic_layer.guard import check_sql

ALLOWED = {"rental", "payment", "film", "film_category", "inventory"}
BLOCKED = {"email", "password", "phone"}


# Test-local limits: the guard has no defaults, callers (service.py) pass policy values.
TEST_MAX_JOINS = 10
TEST_MAX_LIMIT = 10_001


def ok(sql, **kw):
    kw.setdefault("max_joins", TEST_MAX_JOINS)
    kw.setdefault("max_limit", TEST_MAX_LIMIT)
    check_sql(sql, ALLOWED, BLOCKED, **kw)


def bad(sql, match, **kw):
    with pytest.raises(GuardError, match=match):
        ok(sql, **kw)


def test_accepts_plain_select_with_placeholders():
    ok("SELECT COUNT(*) AS n FROM public.rental r WHERE r.rental_id IN (%s, %s) LIMIT 5")


def test_accepts_cte_select():
    ok("WITH a AS (SELECT rental_id FROM rental) SELECT COUNT(*) FROM a LIMIT 1")


@pytest.mark.parametrize("sql", [
    "SELECT 1 FROM rental LIMIT 1; DROP TABLE rental",
    "DELETE FROM rental",
    "UPDATE rental SET return_date = NULL",
    "INSERT INTO rental VALUES (1)",
    "TRUNCATE rental",
    "COPY rental TO '/tmp/x'",
    "SET ROLE postgres",
    "WITH a AS (SELECT 1) DELETE FROM rental",
    "CREATE TABLE t AS SELECT 1",
])
def test_rejects_writes_and_multiple_statements(sql):
    bad(sql, "only SELECT|exactly one")


def test_rejects_select_into_and_locks():
    bad("SELECT rental_id INTO t FROM rental LIMIT 1", "not allowed in a read-only")
    bad("SELECT rental_id FROM rental LIMIT 1 FOR UPDATE", "not allowed in a read-only")


def test_rejects_unlisted_tables_and_schemas():
    bad("SELECT 1 FROM pg_catalog.pg_roles LIMIT 1", "not allowed")
    bad("SELECT 1 FROM staff LIMIT 1", "allow-list")
    bad("SELECT 1 FROM information_schema.columns LIMIT 1", "not allowed")


def test_rejects_dangerous_functions():
    bad("SELECT pg_terminate_backend(1) FROM rental LIMIT 1", "not allowed")
    bad("SELECT set_config('x','y',false) FROM rental LIMIT 1", "not allowed")
    bad("SELECT pg_read_file('/etc/passwd') FROM rental LIMIT 1", "not allowed")
    bad("SELECT nextval('s') FROM rental LIMIT 1", "not allowed")


def test_rejects_blocked_columns_and_star():
    bad("SELECT c.email FROM rental c LIMIT 1", "personal")
    bad("SELECT * FROM rental LIMIT 1", "SELECT \\*")
    bad("SELECT r.* FROM rental r LIMIT 1", "SELECT \\*")


def test_limit_rules():
    bad("SELECT 1 FROM rental", "LIMIT is required")
    bad("SELECT 1 FROM rental LIMIT 20000", "outside")
    ok("SELECT 1 FROM rental", require_limit=False)


def test_join_cap():
    joins = " ".join(f"JOIN film f{i} ON f{i}.film_id = r.film_id" for i in range(12))
    bad(f"SELECT 1 FROM rental r {joins} LIMIT 1", "joins")


def test_limits_are_required_keyword_arguments():
    sql = "SELECT 1 FROM rental LIMIT 1"
    with pytest.raises(TypeError):
        check_sql(sql, ALLOWED, BLOCKED)
    with pytest.raises(TypeError):
        check_sql(sql, ALLOWED, BLOCKED, max_joins=TEST_MAX_JOINS)
    with pytest.raises(TypeError):
        check_sql(sql, ALLOWED, BLOCKED, max_limit=TEST_MAX_LIMIT)


# ====================================================================== policy-driven guard and audit (I5, I6, I7)
import json
import logging

from semantic_layer import SemanticLayer
from semantic_layer.errors import RequestError


def test_service_guard_uses_policy_join_and_limit_caps():
    layer = SemanticLayer()
    pol = layer.defs.policy

    def joined(n):
        joins = " ".join(f"JOIN public.film f{i} ON f{i}.film_id = r.film_id" for i in range(n))
        return f"SELECT 1 FROM public.rental r {joins} LIMIT 1"

    layer.guard(joined(pol.max_joins))
    with pytest.raises(GuardError, match="joins"):
        layer.guard(joined(pol.max_joins + 1))
    layer.guard(f"SELECT 1 FROM public.rental r LIMIT {pol.max_rows + 1}")
    with pytest.raises(GuardError, match="outside"):
        layer.guard(f"SELECT 1 FROM public.rental r LIMIT {pol.max_rows + 2}")


def test_every_compiled_statement_passes_the_guard_with_policy_limits():
    layer = SemanticLayer()
    c = layer.compile({"model": "payment_activity", "measures": ["total_revenue", "payment_count"],
                       "dimensions": ["payment_store", "payment_category"], "limit": 10 ** 6})
    assert c.sql.endswith(f"LIMIT {layer.defs.policy.max_rows + 1}")


def test_personal_data_names_are_never_accepted_as_dimension_or_filter():
    layer = SemanticLayer()
    for name in ("email", "phone", "address", "postal_code", "password", "picture"):
        with pytest.raises(RequestError, match="unknown dimension"):
            layer.compile({"model": "rental_activity", "measures": ["rental_count"], "dimensions": [name]})
        with pytest.raises(RequestError, match="unknown dimension"):
            layer.compile({"model": "rental_activity", "measures": ["rental_count"],
                           "filters": [{"dimension": name, "op": "eq", "value": "x"}]})


class FakeExecutor:
    """Returns canned rows; records every statement it is asked to run."""

    def __init__(self, cols, rows):
        self.cols, self.rows, self.calls = cols, rows, []

    def run(self, sql, params):
        self.calls.append((sql, params))
        if "MAX(r.rental_date)" in sql:
            return ["max"], [["2025-06-30 12:00:00"]]
        return self.cols, self.rows


def audit_entries(caplog):
    return [json.loads(r.getMessage()) for r in caplog.records if r.name == "semantic_layer.audit"]


def test_query_response_and_audit_line_for_a_multi_measure_request(caplog):
    caplog.set_level(logging.INFO, logger="semantic_layer.audit")
    ex = FakeExecutor(["rating", "rental_date", "rental_count", "returned_count"],
                      [["PG", "2025-05-01", 7, 6], ["G", "2025-06-01", 3, 3], ["R", "2025-06-01", 1, 0]])
    layer = SemanticLayer(executor=ex)
    out = layer.query({"model": "rental_activity", "measures": ["rental_count", "returned_count"],
                       "dimensions": ["rating", "rental_date:month"], "limit": 2}, caller="tester")
    assert out["truncated"] is True and out["row_count"] == 2 == len(out["rows"])
    assert [c["name"] for c in out["columns"]] == ["rating", "rental_date", "rental_count", "returned_count"]
    assert [x["name"] for x in out["measure_versions"]] == ["rental_count", "returned_count"]
    assert {"truncated", "partial_period", "null_labels"} <= set(out["caveat_codes"])
    assert out["rows"][0] == {"rating": "PG", "rental_date": "2025-05-01", "rental_count": 7, "returned_count": 6}
    (entry,) = audit_entries(caplog)
    assert entry["caller"] == "tester" and entry["model"] == "rental_activity" and entry["error"] is None
    assert entry["measures"] == ["rental_count", "returned_count"] and entry["dimensions"] == ["rating", "rental_date"]
    assert [x["name"] for x in entry["measure_versions"]] == ["rental_count", "returned_count"]
    assert entry["sql"] == out["sql"] and entry["row_count"] == 2 and entry["duration_ms"] >= 0
    assert set(entry) >= {"caller", "request", "model", "measures", "dimensions", "measure_versions", "definitions",
                          "sql", "row_count", "duration_ms", "error"}
    assert "rows" not in entry and "params" not in entry


def test_audit_line_is_written_for_a_refused_request_and_nothing_executes(caplog):
    caplog.set_level(logging.INFO, logger="semantic_layer.audit")
    ex = FakeExecutor([], [])
    layer = SemanticLayer(executor=ex)
    with pytest.raises(Exception, match="separate questions"):
        layer.query({"model": "payment_activity", "measures": ["total_revenue", "rental_count"]}, caller="tester")
    assert ex.calls == []
    (entry,) = audit_entries(caplog)
    assert entry["error"].startswith("UnsupportedRequest") and entry["sql"] is None and entry["row_count"] is None
    assert entry["measures"] == [] and entry["measure_versions"] == []


def test_catalog_and_policy_expose_the_new_metadata():
    layer = SemanticLayer()
    cat = layer.catalog()
    m = next(x for x in cat["rental_activity"]["measures"] if x["name"] == "rental_count")
    assert m["combinable_with"] == "rental_activity" and "rating" in m["allowed_dimensions"]
    assert all("data_type" in d for model in cat.values() for d in model["dimensions"])
    assert layer.policy() == layer.defs.policy.as_dict() and layer.policy()["max_measures_per_query"] == 5
