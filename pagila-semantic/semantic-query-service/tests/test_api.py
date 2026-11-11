import json
import logging
import threading

import psycopg
import pytest

from semantic_layer import QueryTimeoutError, UnsupportedRequest
from semantic_query_service.__main__ import main
from semantic_query_service.settings import Settings, _parse_keys

from svc_support import KEY_A, KEY_B, REVENUE


# ------------------------------------------------------------------ basics
def test_health_and_catalog(client):
    assert client.get("/health").json()["models"] == ["payment_activity", "rental_activity"]
    cat = client.get("/v1/catalog").json()
    assert "late_return_count" not in {m["name"] for m in cat["rental_activity"]["measures"]}


def test_context_reports_data_end(client):
    r = client.get("/v1/context").json()
    assert r["data_end"] == "2026-07-28" and r["models"]["payment_activity"] == "1.1.0"


def test_compile_does_not_touch_the_database(client, fake):
    r = client.post("/v1/compile", json={**REVENUE, "dimensions": ["rental_date:month"]})
    assert r.status_code == 200
    body = r.json()
    assert body["sql"].startswith("SELECT") and body["params"] and body["definitions_used"]
    assert fake.calls == []


def test_query_returns_data_sql_versions_and_caveats(client, fake):
    r = client.post("/v1/query", json={**REVENUE, "dimensions": ["rental_date:month"]})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == [{"total_revenue": 170962.39}]
    assert body["sql"] and body["definitions_used"]["measure:total_revenue"]
    assert any("Data ends on 2026-07-28" in c for c in body["caveats"])


def test_response_headers(client):
    r = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert r.headers["x-request-id"] == "abc-123" and r.headers["cache-control"] == "no-store"
    assert len(client.get("/health").headers["x-request-id"]) == 32


# ------------------------------------------------------------------ requests that must be refused
def test_sql_in_the_body_is_refused_and_not_echoed(client, fake):
    evil = "SELECT email FROM customer"
    r = client.post("/v1/query", json={**REVENUE, "sql": evil})
    assert r.status_code == 422 and r.json()["error"]["type"] == "invalid_request"
    assert evil not in r.text and fake.calls == []


@pytest.mark.parametrize("body,fragment", [
    ({"model": "payment_activity", "measures": ["nope"]}, "unknown measure"),
    ({"model": "rental_activity", "measures": ["late_return_count"]}, "not approved"),
    ({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["email"]}, "personal data"),
    ({"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["actor_name", "category"]}, "cannot be combined"),
])
def test_semantic_errors_are_422_and_never_reach_the_database(client, fake, body, fragment):
    r = client.post("/v1/query", json=body)
    assert r.status_code == 422 and r.json()["error"]["type"] == "request_not_allowed"
    assert fragment in r.json()["error"]["message"] and fake.calls == []


@pytest.mark.parametrize("body", [
    {"measures": ["total_revenue"]},                                   # no model
    {"model": "payment_activity", "measures": []},                      # empty
    {"model": "payment_activity", "measures": "total_revenue"},         # wrong type
    {"model": "payment_activity", "measures": ["total_revenue"], "limit": 0},
    {"model": "payment_activity", "measures": ["total_revenue"], "filters": [{"dimension": "x", "op": "eq", "extra": 1}]},
    {"model": "payment_activity", "measures": ["total_revenue"], "order_by": [{"field": "total_revenue", "direction": "sideways"}]},
    {"model": "payment_activity", "measures": ["m"] * 21},
])
def test_malformed_bodies_are_422(client, body):
    r = client.post("/v1/query", json=body)
    assert r.status_code == 422 and r.json()["error"]["type"] == "invalid_request"


def test_body_limits(make_client):
    c, _ = make_client(max_body_bytes=200)
    assert c.post("/v1/query", json={**REVENUE, "dimensions": ["x" * 400]}).status_code == 413
    r = c.post("/v1/query", content=iter([b'{"model":"x"}']), headers={"content-type": "application/json"})
    assert r.status_code == 411


# ------------------------------------------------------------------ failures map to the right status
def test_timeout_is_504(client, fake):
    fake.fail = QueryTimeoutError("query exceeded the 30000 ms time limit")
    r = client.post("/v1/query", json=REVENUE)
    assert r.status_code == 504 and r.json()["error"]["type"] == "query_timeout"


def test_database_down_is_503_with_retry_after(client, fake):
    fake.fail = psycopg.OperationalError("connection to server at 10.0.0.5 failed: password=hunter2")
    r = client.post("/v1/query", json=REVENUE)
    assert r.status_code == 503 and r.headers["retry-after"] == "5"
    assert "hunter2" not in r.text and "10.0.0.5" not in r.text


def test_unexpected_errors_do_not_leak_details(client, fake):
    fake.fail = RuntimeError("secret-detail /etc/passwd")
    r = client.post("/v1/query", json=REVENUE)
    assert r.status_code == 500 and "secret-detail" not in r.text
    assert r.json()["error"]["request_id"]


def test_busy_service_sheds_load(make_client, fake):
    c, layer = make_client(max_concurrent_queries=1, queue_wait_seconds=0.05)
    fake.gate = threading.Event()
    first = {}
    t = threading.Thread(target=lambda: first.update(r=c.post("/v1/query", json=REVENUE)))
    t.start()
    assert fake.entered.wait(5)
    r = c.post("/v1/query", json=REVENUE)
    assert r.status_code == 503 and r.json()["error"]["type"] == "busy" and r.headers["retry-after"] == "2"
    fake.gate.set()
    t.join(5)
    assert first["r"].status_code == 200
    assert c.post("/v1/query", json=REVENUE).status_code == 200            # slot was released


# ------------------------------------------------------------------ authentication
def test_auth(make_client, caplog):
    c, _ = make_client(api_keys={KEY_A: "streamlit", KEY_B: "intent-parser"})
    assert c.get("/health").status_code == 200                              # liveness stays open
    for headers in ({}, {"X-API-Key": "wrong-key-0123456789"}, {"X-API-Key": KEY_A[:-1]}):
        r = c.post("/v1/query", json=REVENUE, headers=headers)
        assert r.status_code == 401 and r.headers["www-authenticate"] == "ApiKey"
    assert c.get("/v1/catalog").status_code == 401

    with caplog.at_level(logging.INFO, logger="semantic_layer.audit"):
        assert c.post("/v1/query", json=REVENUE, headers={"X-API-Key": KEY_A}).status_code == 200
        assert c.post("/v1/query", json=REVENUE, headers={"X-API-Key": KEY_B}).status_code == 200
    callers = [json.loads(r.getMessage())["caller"] for r in caplog.records if r.name == "semantic_layer.audit"]
    assert callers == ["streamlit", "intent-parser"]
    assert KEY_A not in caplog.text and KEY_B not in caplog.text


def test_anonymous_when_auth_is_off(client, caplog):
    with caplog.at_level(logging.INFO, logger="semantic_layer.audit"):
        client.post("/v1/query", json=REVENUE)
    assert json.loads(caplog.records[-1].getMessage())["caller"] == "anonymous"


def test_key_parsing():
    assert _parse_keys("a:0123456789abcdef, b:fedcba9876543210") == {"0123456789abcdef": "a", "fedcba9876543210": "b"}
    assert _parse_keys("") == {}
    for bad in ("short:key", "nokey", ":0123456789abcdef"):
        with pytest.raises(ValueError):
            _parse_keys(bad)


def test_refuses_to_listen_publicly_without_keys(monkeypatch, capsys):
    monkeypatch.delenv("SEMANTIC_API_KEYS", raising=False)
    assert main(["--host", "0.0.0.0"]) == 2
    assert "SEMANTIC_API_KEYS" in capsys.readouterr().err


# ------------------------------------------------------------------ validation report
def test_validation_report(make_client, tmp_path):
    c, layer = make_client()
    layer.root = tmp_path
    assert c.get("/v1/validation").status_code == 404
    (tmp_path / "validation" / "results").mkdir(parents=True)
    (tmp_path / "validation" / "results" / "latest.json").write_text('{"summary": {"passed": 1}}')
    assert c.get("/v1/validation").json() == {"summary": {"passed": 1}}


# ------------------------------------------------------------------ multi-measure contract (S3)
POLICY = {"max_measures_per_query": 5, "max_dimensions_per_query": 8, "default_limit": 1000,
          "max_rows": 10000, "max_joins": 10, "max_fan_out_dimensions": 1, "version": "1.0.0"}
MULTI = {"model": "payment_activity", "measures": ["total_revenue", "payment_count"],
         "dimensions": ["rental_date:month"], "order_by": [{"field": "total_revenue", "direction": "desc"}],
         "limit": 10, "timezone": "UTC", "accept_fan_out": False}


def test_new_request_schema_is_accepted(client, fake):
    r = client.post("/v1/compile", json=MULTI)
    assert r.status_code == 200, r.text
    assert client.post("/v1/query", json=MULTI).status_code == 200


def test_legacy_singular_measure_is_422_invalid_request(client, fake):
    r = client.post("/v1/query", json={"model": "payment_activity", "measure": "total_revenue"})
    assert r.status_code == 422 and r.json()["error"]["type"] == "invalid_request"
    assert "measure" in r.json()["error"]["message"] and fake.calls == []
    r = client.post("/v1/compile", json={**REVENUE, "measure": "total_revenue"})
    assert r.status_code == 422 and r.json()["error"]["type"] == "invalid_request"


def test_list_limits_come_from_the_policy(client, fake):
    n = POLICY["max_measures_per_query"]
    ok = client.post("/v1/compile", json={"model": "payment_activity", "measures": ["total_revenue"] * n})
    assert ok.status_code == 422 and ok.json()["error"]["type"] == "request_not_allowed"   # past pydantic: duplicates
    over = client.post("/v1/query", json={"model": "payment_activity", "measures": ["total_revenue"] * (n + 1)})
    assert over.status_code == 422 and over.json()["error"]["type"] == "invalid_request"
    d = POLICY["max_dimensions_per_query"]
    over = client.post("/v1/query", json={**REVENUE, "dimensions": ["rental_date"] * (d + 1)})
    assert over.status_code == 422 and over.json()["error"]["type"] == "invalid_request"
    assert fake.calls == []


def test_limits_follow_a_changed_policy(make_client, monkeypatch):
    c, layer = make_client()
    monkeypatch.setattr(type(layer), "policy", lambda self: {**POLICY, "max_measures_per_query": 2})
    from semantic_query_service import create_app
    from fastapi.testclient import TestClient
    c2 = TestClient(create_app(layer=layer, settings=Settings()), raise_server_exceptions=False)
    r = c2.post("/v1/query", json={"model": "payment_activity", "measures": ["total_revenue", "payment_count", "avg_payment"]})
    assert r.status_code == 422 and r.json()["error"]["type"] == "invalid_request"


def test_query_response_has_columns_versions_row_count(client, fake):
    body = client.post("/v1/query", json=MULTI).json()
    assert [c["name"] for c in body["columns"]] == ["rental_date", "total_revenue", "payment_count"]
    assert {"name", "role", "label", "unit", "type"} <= set(body["columns"][0])
    assert [m["name"] for m in body["measure_versions"]] == ["total_revenue", "payment_count"]
    assert all(m["version"] for m in body["measure_versions"])
    assert body["row_count"] == len(body["rows"]) and body["truncated"] is False
    assert isinstance(body["caveat_codes"], list) and "partial_period" in body["caveat_codes"]


def test_compile_response_has_columns_versions_codes(client, fake):
    body = client.post("/v1/compile", json=MULTI).json()
    assert [c["name"] for c in body["columns"]] == ["rental_date", "total_revenue", "payment_count"]
    assert [m["name"] for m in body["measure_versions"]] == ["total_revenue", "payment_count"]
    assert "caveat_codes" in body and body["limit"] == 10 and fake.calls == []


def test_mixed_models_is_422_unsupported_with_details(client, fake):
    r = client.post("/v1/query", json={"model": "payment_activity", "measures": ["total_revenue", "rental_count"]})
    err = r.json()["error"]
    assert r.status_code == 422 and err["type"] == "unsupported" and err["code"] == "mixed_models"
    assert err["details"] == {"measure_models": {"total_revenue": "payment_activity", "rental_count": "rental_activity"}}
    for text in ("total_revenue", "payment_activity", "rental_count", "rental_activity", "separate questions"):
        assert text in err["message"]
    assert err["request_id"] and fake.calls == []


@pytest.mark.parametrize("code,details", [
    ("not_combinable", {"combinable_with": {"a": "x", "b": "y"}}),
    ("dimension_not_allowed", {"dimension": "d", "blocking_measures": ["a"]}),
    ("grain_not_allowed", {"dimension": "d", "grain": "day", "blocking_measures": ["a"]}),
])
def test_every_unsupported_code_maps_to_422(make_client, monkeypatch, code, details):
    c, layer = make_client()

    def boom(request):
        raise UnsupportedRequest("combinable_with mismatch", code, details)
    monkeypatch.setattr(layer, "compile", boom)
    for path in ("/v1/query", "/v1/compile"):
        r = c.post(path, json=REVENUE)
        err = r.json()["error"]
        assert r.status_code == 422 and err["type"] == "unsupported" and err["code"] == code and err["details"] == details


def test_other_errors_keep_their_mapping_and_have_no_code(client):
    err = client.post("/v1/query", json={"model": "payment_activity", "measures": ["nope"]}).json()["error"]
    assert err["type"] == "request_not_allowed" and "code" not in err and "details" not in err


def test_catalog_carries_combinability_and_allowed_dimensions(client):
    cat = client.get("/v1/catalog").json()
    assert set(cat) == {"payment_activity", "rental_activity"}
    for name, model in cat.items():
        dims = {d["name"] for d in model["dimensions"]}
        for m in model["measures"]:
            assert m["combinable_with"] == name
            assert set(m["allowed_dimensions"]) == dims
    assert "policy" not in cat


def test_context_exposes_the_policy(client, make_client):
    assert client.get("/v1/context").json()["policy"] == POLICY
    layer = make_client()[1]
    assert client.get("/v1/context").json()["policy"] == layer.policy()
