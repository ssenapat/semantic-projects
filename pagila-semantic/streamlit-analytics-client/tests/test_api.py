import json

import httpx
import pytest

from analytics_client.api import ApiError, Config, IntentApi


def api(handler, **cfg):
    return IntentApi(Config(base_url="http://svc", **cfg), transport=httpx.MockTransport(handler))


def test_ask_posts_question_and_previous_request_with_key():
    seen = {}

    def handler(request):
        seen.update(path=request.url.path, key=request.headers.get("x-api-key"), body=json.loads(request.content))
        return httpx.Response(200, json={"status": "ready"})
    out = api(handler, api_key="k" * 20).ask("revenue", {"model": "m"})
    assert out == {"status": "ready"}
    assert seen == {"path": "/v1/ask", "key": "k" * 20, "body": {"question": "revenue", "previous_request": {"model": "m"}}}


def test_no_previous_request_means_no_field():
    seen = {}
    api(lambda r: (seen.update(b=json.loads(r.content)), httpx.Response(200, json={}))[1]).ask("x")
    assert seen["b"] == {"question": "x"}


@pytest.mark.parametrize("status,kind", [(401, "unauthorized"), (503, "busy"), (422, "invalid"), (413, "invalid"), (502, "upstream"), (500, "server")])
def test_http_errors_become_api_errors(status, kind):
    with pytest.raises(ApiError) as exc:
        api(lambda r: httpx.Response(status, json={"error": {"message": "detail from service"}}, headers={"Retry-After": "2"})).ask("x")
    assert exc.value.kind == kind
    assert "Traceback" not in exc.value.message


def test_busy_carries_retry_after():
    with pytest.raises(ApiError) as exc:
        api(lambda r: httpx.Response(503, json={}, headers={"Retry-After": "2"})).ask("x")
    assert exc.value.retry_after == 2.0


def test_unreachable_and_timeout_and_garbage():
    def refuse(request):
        raise httpx.ConnectError("no")

    def slow(request):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(ApiError) as e1:
        api(refuse).ask("x")
    with pytest.raises(ApiError) as e2:
        api(slow).ask("x")
    with pytest.raises(ApiError) as e3:
        api(lambda r: httpx.Response(200, text="<html>")).ask("x")
    assert (e1.value.kind, e2.value.kind, e3.value.kind) == ("unreachable", "timeout", "bad_response")


def test_server_error_text_is_not_trusted_for_unknown_failures():
    with pytest.raises(ApiError) as exc:
        api(lambda r: httpx.Response(500, text="secret stack trace")).ask("x")
    assert "secret" not in exc.value.message


def test_ready():
    assert api(lambda r: httpx.Response(200, json={"status": "ready"})).ready() == (True, "connected")
    ok, msg = api(lambda r: httpx.Response(502, json={})).ready()
    assert not ok and msg


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("ANALYTICS_INTENT_URL", "http://x:9/")
    monkeypatch.setenv("ANALYTICS_REQUEST_TIMEOUT", "7")
    monkeypatch.delenv("ANALYTICS_INTENT_API_KEY", raising=False)
    c = Config.from_env()
    assert (c.base_url, c.timeout, c.api_key) == ("http://x:9", 7.0, None)
