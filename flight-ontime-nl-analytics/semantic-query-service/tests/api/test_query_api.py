import json
from datetime import date


def test_health_and_openapi(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health/ready").json()
    assert r["status"] == "ready" and r["reference_date"] == "2026-01-31" and r["value_maps_loaded"] is True
    assert client.get("/openapi.json").status_code == 200


def test_not_ready_without_password(settings, db):
    from fastapi.testclient import TestClient
    from semantic_query_service.app.main import create_app
    settings.pgpassword = None
    r = TestClient(create_app(settings)).get("/health/ready")
    assert r.status_code == 503 and r.json()["type"].endswith("database-unavailable")
    assert r.json()["detail"] == "PGPASSWORD is not set"


def test_not_ready_invalid_reference_date(settings, executor, capsys):
    from fastapi.testclient import TestClient
    from semantic_query_service.app.main import create_app
    settings.query_reference_date = "31-01-2026"
    capsys.readouterr()
    r = TestClient(create_app(settings, executor)).get("/health/ready")
    assert r.status_code == 503 and r.json()["type"].endswith("database-unavailable")
    assert r.json()["detail"] == "QUERY_REFERENCE_DATE is invalid (expected YYYY-MM-DD)"
    out = capsys.readouterr().out
    recs = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    assert any(x.get("event") == "not_ready" and x.get("error_code") == "database_unavailable" for x in recs)
    assert "31-01-2026" not in out and "s3cret-pw" not in out


def _limit_case(settings, executor, db, body, nrows):
    from fastapi.testclient import TestClient
    from semantic_query_service.app.main import create_app
    db.result = lambda s, p: (["origin_state", "flight_count"], [("S%d" % i, i) for i in range(nrows)])
    c = TestClient(create_app(settings, executor))
    bd = body()
    bd["catalog_version"] = c.get("/health/ready").json()["catalog_version"]
    bd["semantic_request"]["group_by"] = [{"dimension": "origin_state"}]
    bd["semantic_request"]["requested_limit"] = 5000
    return c.post("/api/v1/query/execute", json=bd).json()


def test_row_cap_reached_is_truncated(settings, executor, db, body):
    j = _limit_case(settings, executor, db, body, 1001)     # SQL fetches effective_limit + 1 rows
    assert j["query_execution"]["effective_limit"] == 1000 and j["row_count"] == 1000
    assert len(j["rows"]) == 1000 and j["truncated"] is True


def test_below_row_cap_not_truncated(settings, executor, db, body):
    j = _limit_case(settings, executor, db, body, 1000)     # exactly the limit: nothing was cut
    assert j["row_count"] == 1000 and j["truncated"] is False


def test_kpi_result(client, body, db):
    r = client.post("/api/v1/query/execute", json=body())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["outcome"] == "result" and j["request_id"] == "r1"
    assert j["rows"] == [[42]] and j["columns"][0]["semantic_type"] == "metric"
    assert j["columns"][0] == {"name": "flight_count", "role": "metric", "label": j["columns"][0]["label"],
                               "unit": "count", "semantic_type": "metric", "data_type": "integer"}
    assert j["truncated"] is False and j["metric_versions"] == {"flight_count": 1}
    assert j["chart_hints"][0]["chart_type"] == "kpi" and j["chart_hints"][-1]["chart_type"] == "table"
    qe = j["query_execution"]
    assert qe["sql_guard"] == "passed" and qe["reference_date"] == "2026-01-31"
    assert set(qe) == {"elapsed_ms", "effective_limit", "reference_date", "resolved_date_range",
                       "explain_checked", "sql_guard"}
    assert "ontime" not in r.text and "SELECT" not in r.text.upper().replace("SELECTED", "")
    assert j["metadata"]["metrics"] == ["flight_count"] and j["row_count"] == 1
    assert set(j["metadata"]) == {"metrics", "catalog_version", "time_basis", "applied_filters",
                                  "generated_at_utc"}


def test_time_series_with_date_range(client, body, db):
    db.result = lambda s, p: (["flight_date__month", "flight_count"],
                              [(date(2025, 12, 1), 10), (date(2026, 1, 1), 12)])
    r = client.post("/api/v1/query/execute", json=body(
        group_by=[{"dimension": "flight_date", "time_grain": "month"}],
        date_range={"kind": "relative", "relative_period": "last_30_days"}))
    j = r.json()
    assert r.status_code == 200 and j["outcome"] == "result", r.text
    assert j["rows"][0][0] == "2025-12-01" and j["columns"][0]["name"] == "flight_date__month"
    assert j["chart_hints"][0]["chart_type"] == "line"
    assert j["query_execution"]["resolved_date_range"]["end_exclusive"]
    assert all(s[0].lstrip().upper().startswith(("SELECT", "WITH")) for s in db.statements)


def test_unknown_metric_is_200_unsupported(client, body):
    r = client.post("/api/v1/query/execute", json=body(metrics=["nope"]))
    assert r.status_code == 200 and r.json()["outcome"] == "unsupported"


def test_chicago_clarifies(client, body):
    r = client.post("/api/v1/query/execute", json=body(
        filters=[{"dimension": "origin_airport_code", "operator": "equals", "values": ["Chicago"]}]))
    assert r.status_code == 200 and r.json()["outcome"] == "clarification_required"
    assert "query_service_clarification_total 1" in client.get("/metrics").text


def test_errors(client, body, db):
    b = body()
    for extra in ({"raw_sql": "DROP TABLE x"}, {"table": "t"}):
        r = client.post("/api/v1/query/execute", json={**b, **extra})
        assert r.status_code == 422 and r.json()["type"].endswith("query-validation-failed")
    assert client.post("/api/v1/query/execute", content="nope").status_code == 422
    r = client.post("/api/v1/query/execute", json={**b, "catalog_version": "sp1-other"})
    assert r.status_code == 409 and r.json()["request_id"] == "r1"
    assert set(r.json()) == {"type", "title", "status", "detail", "request_id"}

    class QueryCanceled(Exception):
        pass
    db.error = QueryCanceled("canceling statement due to statement timeout")
    assert client.post("/api/v1/query/execute", json=b).status_code == 504
    db.error = RuntimeError("syntax error near secret_table")
    r = client.post("/api/v1/query/execute", json=b)
    assert r.status_code == 500 and "secret_table" not in r.text
    db.error = None
    db.connect_error = RuntimeError("down")
    assert client.post("/api/v1/query/execute", json=b).status_code == 503


def test_guard_rejection_422(client, body, monkeypatch):
    from semantic_query_service.app.services import sql_compiler
    monkeypatch.setattr(sql_compiler, "compile_plan", lambda *a, **k: sql_compiler.CompiledQuery(
        sql="DELETE FROM ontime.flight", params={}, effective_limit=1))
    r = client.post("/api/v1/query/execute", json=body())
    assert r.status_code == 422 and r.json()["type"].endswith("sql-guard-rejected")
    assert "DELETE" not in r.text and "query_service_sql_guard_rejections_total 1" in client.get("/metrics").text


def test_payload_truncation(settings, executor, db, body):
    from fastapi.testclient import TestClient
    from semantic_query_service.app.main import create_app
    settings.query_max_payload_bytes = 300
    db.result = lambda s, p: (["origin_state", "flight_count"], [("S%02d" % i, i) for i in range(100)])
    c = TestClient(create_app(settings, executor))
    bd = body()
    bd["catalog_version"] = c.get("/health/ready").json()["catalog_version"]
    bd["semantic_request"]["group_by"] = [{"dimension": "origin_state"}]
    j = c.post("/api/v1/query/execute", json=bd).json()
    assert j["truncated"] is True and 0 < j["row_count"] < 100


def test_metrics_audit_and_logs_have_no_secrets(client, body, settings, capsys):
    client.post("/api/v1/query/execute", json=body())
    out = capsys.readouterr().out
    rec = json.loads(out.strip().splitlines()[-1])
    assert {"timestamp", "level", "service_name", "service_version", "request_id", "catalog_version",
            "outcome", "latency_ms", "event"} <= set(rec)
    text = client.get("/metrics").text
    for name in ("query_service_requests_total", "query_service_validation_failures_total",
                 "query_service_clarification_total", "query_service_sql_guard_rejections_total",
                 "query_service_db_latency_ms", "query_service_result_rows_total"):
        assert name + " " in text
    audit = open(settings.query_audit_log_path).read()
    a = json.loads(audit.splitlines()[-1])
    assert {"request_id", "catalog_version", "metrics", "metric_versions", "group_by", "filter_dimensions",
            "outcome", "row_count", "elapsed_ms", "timestamp"} == set(a)
    assert "s3cret" not in audit + out and "SELECT" not in audit + out
