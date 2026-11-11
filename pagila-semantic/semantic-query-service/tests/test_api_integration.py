"""Against the real pagila database. Skipped unless PAGILA_PG_USER and PAGILA_PG_PASSWORD are set."""
import pytest

pytestmark = pytest.mark.integration


def test_ready(db_client):
    assert db_client.get("/ready").json() == {"status": "ready"}


def test_revenue_matches_baseline(db_client):
    r = db_client.post("/v1/query", json={"model": "payment_activity", "measures": ["total_revenue", "payment_count"]})
    row = r.json()["rows"][0]
    assert row["total_revenue"] == pytest.approx(170962.39, abs=0.005) and row["payment_count"] == 51061


def test_truncation_and_limit(db_client):
    r = db_client.post("/v1/query", json={"model": "rental_activity", "measures": ["rental_count"],
                                          "dimensions": ["rental_date:day"], "limit": 3}).json()
    assert r["truncated"] and r["row_count"] == 3


def test_personal_data_is_refused(db_client):
    r = db_client.post("/v1/query", json={"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["email"]})
    assert r.status_code == 422


def test_real_validation_report(db_client):
    r = db_client.get("/v1/validation")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 90 and body["passed"] <= body["total"]
