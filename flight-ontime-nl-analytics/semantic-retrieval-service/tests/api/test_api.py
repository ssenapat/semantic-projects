import json
import logging

import httpx

from semantic_retrieval_service.app.schemas.errors import ApiError
from semantic_retrieval_service.app.schemas.parser import IntentParserRequest
from semantic_retrieval_service.app.schemas.result import QueryExecuteRequest
from semantic_retrieval_service.app.services.parser_client import ParserClient
from semantic_retrieval_service.app.services.query_service_client import QueryServiceClient
from semantic_retrieval_service.app.services.rate_limiter import RateLimiter

from .conftest import QUESTION, StubParser, StubQuery

ENV_KEYS = {"type", "title", "status", "detail", "request_id"}


def ask(c, **kw):
    return c.post("/api/v1/ask", json={"question": QUESTION, **kw})


def test_ask_result(make_client):
    parser, query = StubParser(), StubQuery()
    r = ask(make_client(parser, query), requested_limit=50)
    assert r.status_code == 200
    b = r.json()
    assert b["outcome"] == "result" and b["authorization_mode"] == "permissive_placeholder"
    assert b["result"]["outcome"] == "result" and b["clarification"] is None and b["unsupported"] is None
    assert b["request_id"] == r.headers["X-Request-ID"] == parser.calls[0].request_id == query.calls[0].request_id
    assert query.calls[0].semantic_request.requested_limit == 50  # min(ask, parser)
    assert b["retrieval_summary"]["selected_domains"]


def test_ask_parser_clarification_and_unsupported(make_client):
    q = StubQuery()
    b = ask(make_client(StubParser("clarification_required"), q)).json()
    assert b["outcome"] == "clarification_required" and b["clarification"]["question"] and not q.calls
    b = ask(make_client(StubParser("unsupported"), q)).json()
    assert b["outcome"] == "unsupported" and b["unsupported"]["reason"] == "no_approved_metric"


def test_ask_query_relays(make_client):
    assert ask(make_client(query=StubQuery("clarification_required"))).json()["outcome"] == "clarification_required"
    assert ask(make_client(query=StubQuery("unsupported"))).json()["outcome"] == "unsupported"


def test_validation_errors_are_400_envelopes(make_client):
    c = make_client()
    for body in ({"question": "   "}, {"question": ""}, {"question": "x" * 4001}, {"question": "a", "bogus": 1},
                 {"question": "a", "requested_limit": 5001}, {}):
        r = c.post("/api/v1/ask", json=body)
        assert r.status_code == 400 and set(r.json()) == ENV_KEYS
        assert r.json()["type"].endswith("/invalid-request")


def test_no_retrievable_context_422(make_client):
    r = make_client().post("/api/v1/retrieve", json={"question": "zzqqxx"})
    assert r.status_code == 422 and r.json()["type"].endswith("/no-retrievable-context")


def test_downstream_error_mapping(make_client):
    for exc, status, code in [
        (ApiError("intent_parser_unavailable", "x"), 424, "intent-parser-unavailable"),
        (ApiError("catalog_version_mismatch", "x"), 409, "catalog-version-mismatch"),
        (ApiError("rate_limited", "x"), 429, "rate-limited")]:
        r = ask(make_client(StubParser(exc)))
        assert r.status_code == status and r.json()["type"].endswith(code) and r.json()["request_id"]
    r = ask(make_client(query=StubQuery(ApiError("query_service_unavailable", "x"))))
    assert r.status_code == 424


def test_catalog_version_mismatch_from_parser(make_client):
    p = StubParser()
    p.catalog_version = "sp1-other"
    assert ask(make_client(p)).status_code == 409


def test_rate_limit_local(make_client):
    c = make_client(retriever_rate_limit_per_minute=2)
    codes = [c.post("/api/v1/retrieve", json={"question": QUESTION}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_rate_limiter_window():
    t = [0.0]
    rl = RateLimiter(1, clock=lambda: t[0])
    assert rl.allow() and not rl.allow()
    t[0] = 60.0
    assert rl.allow()


def test_retrieve_no_downstream_calls(make_client):
    p, q = StubParser(), StubQuery()
    r = make_client(p, q).post("/api/v1/retrieve", json={"question": QUESTION})
    b = r.json()
    assert r.status_code == 200 and set(b) == {"request_id", "catalog_version", "authorization_mode",
                                               "semantic_context", "retrieval_summary"}
    assert not p.calls and not q.calls


def test_catalog_status_and_metrics(make_client):
    c = make_client()
    s = c.get("/api/v1/catalog/status").json()
    assert s["ready"] is True and s["catalog_version"].startswith("sp1-") and "counts_by_status" in s
    m = c.get("/api/v1/catalog/metrics").json()
    assert m["metrics"] and [x["name"] for x in m["metrics"]] == sorted(x["name"] for x in m["metrics"])
    text = json.dumps(m).lower()
    assert "select " not in text and "ontime." not in text


def test_health_and_metrics_endpoint(make_client):
    c = make_client()
    assert c.get("/health/live").json() == {"status": "ok"}
    r = c.get("/health/ready").json()
    assert r["status"] == "ready" and r["catalog_version"]
    c.post("/api/v1/retrieve", json={"question": QUESTION})
    t = c.get("/metrics")
    assert t.headers["content-type"].startswith("text/plain")
    lines = dict(l.split(" ") for l in t.text.strip().splitlines())
    assert int(lines["semantic_retriever_requests_total"]) == 1
    assert {"semantic_retriever_retrieval_latency_ms", "semantic_retriever_context_tokens_estimate",
            "semantic_retriever_low_confidence_total"} <= set(lines)
    assert c.get("/openapi.json").status_code == 200


def test_not_ready_when_catalog_missing(make_client):
    c = make_client(ready=False)
    assert c.get("/health/live").status_code == 200
    for path in ("/health/ready", "/api/v1/catalog/status", "/api/v1/catalog/metrics"):
        r = c.get(path)
        assert r.status_code == 503 and r.json()["type"].endswith("/catalog-not-ready")
    assert c.post("/api/v1/ask", json={"question": QUESTION}).status_code == 503


def test_logs_have_request_id_and_no_question(make_client, capfd):
    c = make_client()
    logging.getLogger("semantic_retrieval_service")
    r = ask(c)
    out = capfd.readouterr().out
    rec = [json.loads(l) for l in out.splitlines() if l.startswith("{")][-1]
    assert rec["request_id"] == r.json()["request_id"] and rec["event"] == "ask" and rec["outcome"] == "result"
    assert {"timestamp", "level", "service_name", "service_version", "catalog_version", "semantic_version",
            "latency_ms", "question_sha256", "question_chars"} <= set(rec)
    assert QUESTION not in out


def _ctx(retriever):
    ctx, _ = retriever.retrieve(QUESTION, [], "rid")
    return ctx


def test_parser_client_mapping(retriever):
    req = IntentParserRequest(request_id="rid", question=QUESTION, semantic_context=_ctx(retriever))

    def client(handler):
        return ParserClient("http://p", 1, httpx.Client(transport=httpx.MockTransport(handler)))

    def status(code):
        return client(lambda r: httpx.Response(code, json={}))

    for code, expect in ((500, 424), (409, 409), (429, 429), (404, 424)):
        try:
            status(code).parse(req)
            assert False
        except ApiError as e:
            assert e.status == expect and e.request_id == "rid"

    def boom(request):
        raise httpx.ConnectTimeout("t")
    try:
        client(boom).parse(req)
        assert False
    except ApiError as e:
        assert e.code == "intent_parser_unavailable"
    ok = client(lambda r: httpx.Response(200, json={
        "outcome": "unsupported", "catalog_version": "v", "confidence": 0.1,
        "unsupported": {"message": "m", "reason": "no_approved_metric"}}))
    assert ok.parse(req).outcome == "unsupported"
    try:
        client(lambda r: httpx.Response(200, json={"outcome": "nope"})).parse(req)
        assert False
    except ApiError as e:
        assert e.status == 424


def test_query_client_mapping_and_header():
    seen = {}

    def handler(request):
        seen["rid"] = request.headers["X-Request-ID"]
        return httpx.Response(503, json={})
    c = QueryServiceClient("http://q", 1, httpx.Client(transport=httpx.MockTransport(handler)))
    req = QueryExecuteRequest(request_id="r1", catalog_version="v", semantic_request={"metrics": ["m"]})
    try:
        c.execute(req)
        assert False
    except ApiError as e:
        assert e.code == "query_service_unavailable" and e.status == 424
    assert seen["rid"] == "r1"
