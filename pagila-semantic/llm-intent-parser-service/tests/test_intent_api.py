import httpx
import pytest

from intent_parser import IntentParser, create_app
from intent_parser.llm import LLMError
from intent_parser.query_client import QueryServiceClient
from intent_parser.settings import Settings, parse_keys
from fastapi.testclient import TestClient

from support import ScriptedLLM, submit, unsupported, clarify

OK = dict(model="payment_activity", measures=["total_revenue"], assumptions=["Dated by rental date."])
KEY = "intent-key-0123456789"


def post(c, path, **body):
    return c.post(path, json=body)


def test_parse_does_not_execute(make_client, executor):
    c, _, _ = make_client(submit(**OK))
    r = post(c, "/v1/parse", question="total revenue")
    assert r.status_code == 200 and r.json()["status"] == "ready" and "answer" not in r.json()
    assert not any("payment" in s for s in executor.calls)


def test_ask_executes_through_the_query_service(make_client, executor):
    c, _, _ = make_client(submit(**OK))
    body = post(c, "/v1/ask", question="total revenue").json()
    assert body["status"] == "ready" and body["answer"]["rows"] == [{"total_revenue": 170962.39}]
    assert body["answer"]["definitions_used"]["measure:total_revenue"]


@pytest.mark.parametrize("turn", [unsupported("no profit data"), clarify("Which?", ["a"])])
def test_ask_does_not_execute_when_not_ready(make_client, executor, turn):
    c, _, _ = make_client(turn)
    body = post(c, "/v1/ask", question="profit?").json()
    assert body["status"] != "ready" and "answer" not in body
    assert all("MAX(" in sql for sql in executor.calls)            # only the data-end lookup, never the question


def test_follow_up_is_forwarded(make_client):
    c, llm, _ = make_client(submit(**OK))
    post(c, "/v1/parse", question="and by store?", previous_request={"model": "payment_activity", "measures": ["total_revenue"]})
    assert "<previous_request>" in llm.calls[0][1][0]["content"]


def test_request_id_flows_to_the_query_service(make_client, query_http):
    c, _, _ = make_client(submit(**OK))
    r = c.post("/v1/parse", json={"question": "total revenue"}, headers={"X-Request-ID": "trace-77"})
    assert r.headers["x-request-id"] == "trace-77"


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": 5}, {"question": "x", "sql": "select 1"},
                                  {"question": "x", "previous_request": "not a dict"}])
def test_bad_bodies_are_422(make_client, body):
    c, llm, _ = make_client()
    r = c.post("/v1/parse", json=body)
    assert r.status_code == 422 and r.json()["error"]["type"] == "invalid_request" and llm.calls == []


def test_limits_protect_the_model_budget(make_client):
    c, llm, _ = make_client(max_question_chars=50, max_body_bytes=500)
    r = post(c, "/v1/parse", question="x" * 60)
    assert r.status_code == 422 and r.json()["error"]["type"] == "question_too_long"
    assert post(c, "/v1/parse", question="x" * 600).status_code == 413
    assert llm.calls == []


def test_model_outage_is_502_without_details(make_client):
    c, _, _ = make_client(LLMError("APIConnectionError"))
    r = post(c, "/v1/parse", question="revenue")
    assert r.status_code == 502 and r.json()["error"]["type"] == "llm_unavailable" and "APIConnection" not in r.text


def test_query_service_outage_is_502(make_parser):
    def boom(request):
        raise httpx.ConnectError("refused")
    down = httpx.Client(base_url="http://down", transport=httpx.MockTransport(boom))
    parser = IntentParser(ScriptedLLM(submit(**OK)), QueryServiceClient(client=down), Settings())
    c = TestClient(create_app(parser=parser, settings=Settings()), raise_server_exceptions=False)
    r = post(c, "/v1/parse", question="revenue")
    assert r.status_code == 502 and r.json()["error"]["type"] == "upstream_unavailable"


def test_query_service_rejecting_our_key_is_502_not_401(make_parser):
    deny = httpx.Client(base_url="http://q", transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    parser = IntentParser(ScriptedLLM(), QueryServiceClient(client=deny), Settings())
    c = TestClient(create_app(parser=parser, settings=Settings()), raise_server_exceptions=False)
    assert post(c, "/v1/parse", question="revenue").status_code == 502


def test_busy_service_sheds_load(make_client):
    import threading
    c, llm, _ = make_client(submit(**OK), submit(**OK), max_concurrent=1, queue_wait_seconds=0.05)
    llm.gate = threading.Event()
    first = {}
    t = threading.Thread(target=lambda: first.update(r=post(c, "/v1/parse", question="a")))
    t.start()
    assert llm.entered.wait(5)
    r = post(c, "/v1/parse", question="b")
    assert r.status_code == 503 and r.json()["error"]["type"] == "busy"
    llm.gate.set(); t.join(5)
    assert first["r"].status_code == 200


def test_auth(make_client):
    c, llm, _ = make_client(submit(**OK), app_settings=Settings(api_keys={KEY: "streamlit"}))
    assert c.get("/health").status_code == 200
    for h in ({}, {"X-API-Key": "wrong-key-0123456789"}):
        assert c.post("/v1/parse", json={"question": "x"}, headers=h).status_code == 401
    assert llm.calls == []
    assert c.post("/v1/parse", json={"question": "revenue"}, headers={"X-API-Key": KEY}).status_code == 200


def test_key_parsing():
    assert parse_keys("a:0123456789abcdef") == {"0123456789abcdef": "a"}
    with pytest.raises(ValueError):
        parse_keys("a:short")


def test_main_refuses_public_listen_without_keys(monkeypatch, capsys):
    from intent_parser.__main__ import main
    monkeypatch.delenv("INTENT_API_KEYS", raising=False)
    assert main(["--host", "0.0.0.0"]) == 2
    monkeypatch.delenv("INTENT_MODEL", raising=False)
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "bedrock")
    assert main([]) == 2 and "INTENT_MODEL" in capsys.readouterr().err
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("INTENT_MODEL", "some-model")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    assert main([]) == 2 and "ANTHROPIC_API_KEY" in capsys.readouterr().err
    monkeypatch.setenv("INTENT_LLM_PROVIDER", "nonsense")
    assert main([]) == 2 and "INTENT_LLM_PROVIDER" in capsys.readouterr().err


def test_bedrock_failures_become_llm_errors():
    """An expired SSO token or missing credentials must surface as LLMError, never as a raw botocore error."""
    from botocore.exceptions import TokenRetrievalError
    from intent_parser.llm import BedrockClient, LLMError
    c = BedrockClient("some-profile-id", 256, 5.0, region="us-east-1")

    class Boom:
        def create(self, **kw):
            raise TokenRetrievalError(provider="sso", error_msg="expired")
    c._client.messages = Boom()
    with pytest.raises(LLMError) as e:
        c.complete("sys", [{"role": "user", "content": "q"}], [])
    assert "expired" not in str(e.value)


def test_tool_choice_falls_back_when_model_refuses_forcing():
    import anthropic
    from types import SimpleNamespace as NS
    from intent_parser.llm import BedrockClient
    c = BedrockClient("m", 256, 5.0, region="us-east-1")
    seen = []

    class Fake:
        def create(self, **kw):
            seen.append(kw["tool_choice"]["type"])
            if kw["tool_choice"]["type"] == "any":
                raise anthropic.BadRequestError("tool_choice: type any is not supported", response=NS(
                    status_code=400, headers={}, request=None), body=None)
            return NS(content=[NS(type="tool_use", name="not_supported", input={}, id="t1")],
                      usage=NS(input_tokens=1, output_tokens=1))
    c._client.messages = Fake()
    assert c.complete("s", [], []).tool_name == "not_supported"
    c.complete("s", [], [])
    assert seen == ["any", "auto", "auto"]      # the retry happens once, then auto is remembered
