import pytest
from fastapi.testclient import TestClient

from semantic_retrieval.app import create_app
from semantic_retrieval.index import CatalogIndex

CATALOG = {
    "payment_activity": {"measures": [{"name": "total_revenue", "label": "Total Revenue",
                                       "description": "Sum of payments", "unit": "usd",
                                       "combinable_with": "payment_activity",
                                       "allowed_dimensions": ["store_id", "payment_store"]}],
                         "dimensions": [{"name": "store_id", "label": "Store",
                                         "description": "Store that took the payment", "type": "categorical"},
                                        {"name": "payment_store", "label": "Payment Store",
                                         "description": "Store of the payment", "type": "categorical"}]},
    "rental_activity": {"measures": [{"name": "rental_count", "label": "Rentals",
                                      "description": "Number of distinct rentals", "unit": "count",
                                      "combinable_with": "rental_activity",
                                      "allowed_dimensions": ["film_category", "category", "store_id"]}],
                        "dimensions": [{"name": "film_category", "label": "Film Category",
                                        "description": "Category of the film", "type": "categorical"},
                                       {"name": "category", "label": "Category",
                                        "description": "Rental category", "type": "categorical"},
                                       {"name": "store_id", "label": "Store",
                                        "description": "Rental store", "type": "categorical"}]},
}
POLICY = {"max_measures_per_query": 5, "max_dimensions_per_query": 8}


class Fake:
    def get(self):
        return CATALOG

    def policy(self):
        return POLICY


def test_synonym_sales_finds_revenue():
    hits = CatalogIndex(CATALOG).search("sales by store")
    names = [e.name for e, _ in hits]
    assert {"total_revenue", "store_id"} <= set(names[:4])  # fixture has several store dimensions


def test_genre_finds_category():
    assert CatalogIndex(CATALOG).search("most popular genres")[0][0].name in {"film_category", "rental_count"}


def test_api_and_no_match():
    c = TestClient(create_app(Fake()))
    r = c.post("/v1/retrieve", json={"question": "revenue per month"})
    assert r.status_code == 200 and "payment_activity" in r.json()["models"]
    assert c.post("/v1/retrieve", json={"question": "zzzz qqqq"}).status_code == 422
    assert c.post("/v1/retrieve", json={"question": ""}).status_code == 422
    assert c.get("/health").json() == {"status": "ok"}


def test_glossary_file_loads(tmp_path):
    from semantic_retrieval.index import load_glossary
    f = tmp_path / "g.json"
    f.write_text('{"synonyms": {"cash": ["revenue"]}}')
    assert load_glossary(str(f)) == {"cash": ["revenue"]}


def test_multi_term_question_returns_every_mentioned_measure_and_dimension():
    c = TestClient(create_app(Fake()))
    r = c.post("/v1/retrieve", json={"question": "total revenue and rental count by film category"})
    assert r.status_code == 200
    names = {m["name"] for m in r.json()["matches"]}
    assert {"total_revenue", "rental_count", "film_category"} <= names
    m = next(m for m in r.json()["matches"] if m["name"] == "rental_count")
    assert m["combinable_with"] == "rental_activity" and "film_category" in m["allowed_dimensions"]


def test_ambiguous_terms_listed_and_in_matches():
    c = TestClient(create_app(Fake()))
    body = c.post("/v1/retrieve", json={"question": "revenue by store", "limit": 1}).json()
    terms = {t["term"]: t["matches"] for t in body["ambiguous_terms"]}
    assert terms["store"] == [{"model": "payment_activity", "name": "payment_store"},
                              {"model": "payment_activity", "name": "store_id"},
                              {"model": "rental_activity", "name": "store_id"}]
    have = {(m["model"], m["name"]): m for m in body["matches"]}
    for t in body["ambiguous_terms"]:
        for m in t["matches"]:
            assert (m["model"], m["name"]) in have
    assert set(body["models"]) == {m["model"] for m in body["matches"]} == set(body["catalog"])
    assert any(m["score"] == 0.0 for m in body["matches"])


def test_no_ambiguous_word_gives_empty_list():
    r = TestClient(create_app(Fake())).post("/v1/retrieve", json={"question": "revenue per month"})
    assert r.json()["ambiguous_terms"] == []


def test_default_limit_comes_from_policy(monkeypatch):
    seen = []
    orig = CatalogIndex.search
    monkeypatch.setattr(CatalogIndex, "search", lambda self, q, limit=10: seen.append(limit) or orig(self, q, limit))
    c = TestClient(create_app(Fake()))
    c.post("/v1/retrieve", json={"question": "revenue"})
    c.post("/v1/retrieve", json={"question": "revenue", "limit": 3})
    assert seen == [13, 3]


def test_policy_missing_is_503():
    class NoPolicy(Fake):
        def policy(self):
            from semantic_retrieval.query_client import CatalogUnavailable
            raise CatalogUnavailable("KeyError")
    assert TestClient(create_app(NoPolicy())).post("/v1/retrieve", json={"question": "revenue"}).status_code == 503


def test_auth_and_validation(monkeypatch):
    monkeypatch.setenv("RETRIEVAL_API_KEYS", "secret-key")
    c = TestClient(create_app(Fake()))
    assert c.post("/v1/retrieve", json={"question": "revenue"}).status_code == 401
    assert c.post("/v1/retrieve", json={"question": "revenue", "limit": 51},
                  headers={"X-API-Key": "secret-key"}).status_code == 422
    assert c.post("/v1/retrieve", json={"question": "revenue"},
                  headers={"X-API-Key": "secret-key"}).status_code == 200


def test_keys_not_logged(monkeypatch, caplog):
    monkeypatch.setenv("RETRIEVAL_API_KEYS", "secret-key")
    caplog.set_level("DEBUG")
    TestClient(create_app(Fake())).post("/v1/retrieve", json={"question": "revenue"},
                                        headers={"X-API-Key": "secret-key"})
    assert "secret-key" not in caplog.text


def test_shipped_glossary_ambiguous():
    from semantic_retrieval.index import load_ambiguous
    a = load_ambiguous()
    for w in ("store", "stores", "shop", "branch"):
        assert a[w] == ["store_id", "payment_store"]
    for w in ("category", "genre"):
        assert a[w] == ["category", "payment_category"]
