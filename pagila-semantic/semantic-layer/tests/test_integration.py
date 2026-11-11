"""Run against the real pagila database. Skipped unless PAGILA_PG_USER and PAGILA_PG_PASSWORD are set."""
import pytest

from semantic_layer.errors import SemanticError

pytestmark = pytest.mark.integration


def rows(layer, **req):
    return layer.query({"limit": 10000, **req}, caller="test")["rows"]


def test_total_revenue_matches_baseline(db_layer):
    r = rows(db_layer, model="payment_activity", measures=["total_revenue"])
    assert r[0]["total_revenue"] == pytest.approx(170962.39, abs=0.005)


def test_revenue_by_rental_month_sums_to_total(db_layer):
    r = rows(db_layer, model="payment_activity", measures=["total_revenue"], dimensions=["rental_date:month"])
    assert sum(x["total_revenue"] for x in r) == pytest.approx(170962.39, abs=0.01)


def test_category_allocation_preserves_totals(db_layer):
    cats = rows(db_layer, model="rental_activity", measures=["rental_count"], dimensions=["category"])
    assert len(cats) == 16
    assert sum(x["rental_count"] for x in cats) == pytest.approx(51805, abs=0.01)
    assert any(x["rental_count"] != int(x["rental_count"]) for x in cats)     # fractional by design
    rev = rows(db_layer, model="payment_activity", measures=["total_revenue"], dimensions=["payment_category"])
    assert sum(x["total_revenue"] for x in rev) == pytest.approx(170962.39, abs=0.01)


def test_only_stores_in_use_are_visible(db_layer):
    stores = rows(db_layer, model="rental_activity", measures=["rental_count"], dimensions=["store_id"])
    assert sorted(x["store_id"] for x in stores) == [1, 2]
    assert sum(x["rental_count"] for x in stores) == 51805


def test_open_plus_returned_equals_all(db_layer):
    r = rows(db_layer, model="rental_activity", measures=["rental_count", "returned_count", "open_rental_count"])[0]
    assert r["returned_count"] + r["open_rental_count"] == r["rental_count"] == 51805
    assert r["open_rental_count"] == 241


def test_null_rating_label_and_distinct_ratings(db_layer):
    r = rows(db_layer, model="rental_activity", measures=["rental_count"], dimensions=["rating"])
    assert {x["rating"] for x in r} <= {"G", "PG", "PG-13", "R", "NC-17", "Unknown"}


def test_date_range_filter_is_inclusive_of_the_last_day(db_layer):
    r = db_layer.query({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["rental_date:day"],
                        "filters": [{"dimension": "rental_date", "op": "between", "value": ["2025-01-01", "2025-01-03"]}],
                        "order_by": [{"field": "rental_date", "direction": "asc"}]})
    days = [x["rental_date"] for x in r["rows"]]
    assert days == ["2025-01-01", "2025-01-02", "2025-01-03"]


def test_session_is_read_only(db_layer):
    with pytest.raises(Exception, match="read-only"):
        db_layer.executor.run("CREATE TEMP TABLE t_should_fail AS SELECT 1", [])


def test_guard_blocks_before_database(db_layer):
    from semantic_layer.errors import GuardError
    with pytest.raises(GuardError):
        db_layer.execute_sql("SELECT email FROM public.customer LIMIT 1", [])
    with pytest.raises(GuardError):
        db_layer.execute_sql("DELETE FROM public.rental", [])


def test_truncation_is_reported(db_layer):
    r = db_layer.query({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["rental_date:day"], "limit": 5})
    assert r["truncated"] is True and r["row_count"] == 5 and any("truncated" in c.lower() for c in r["caveats"])


def test_late_measure_is_unavailable(db_layer):
    with pytest.raises(SemanticError, match="not approved"):
        db_layer.query({"model": "rental_activity", "measures": ["late_return_count"]})


def test_multi_measure_rows_have_one_column_per_field(db_layer):
    r = db_layer.query({"model": "payment_activity", "measures": ["total_revenue", "payment_count"],
                        "dimensions": ["rental_date:month"], "limit": 10000})
    assert [c["name"] for c in r["columns"]] == ["rental_date", "total_revenue", "payment_count"]
    assert sum(x["total_revenue"] for x in r["rows"]) == pytest.approx(170962.39, abs=0.01)
    assert [m["name"] for m in r["measure_versions"]] == ["total_revenue", "payment_count"]
    totals = [x["total_revenue"] for x in r["rows"]]
    assert totals == sorted(totals, reverse=True)         # default order: first measure DESC
