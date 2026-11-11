"""HTTP client for the Retriever (CONTRACT §6.7)."""
from __future__ import annotations

from typing import Any, List, Optional

import httpx
from pydantic import ValidationError

from ..models.api_models import QueryResult

# Test hook: tests set this to an httpx.MockTransport used by make_client().
TRANSPORT_OVERRIDE: Optional[httpx.BaseTransport] = None


def _code_from_type(type_url: Any) -> Optional[str]:
    """ErrorEnvelope.type is https://.../errors/<code-with-dashes>; code = last segment, '-' -> '_'."""
    if not isinstance(type_url, str):
        return None
    segment = type_url.rstrip("/").rsplit("/", 1)[-1]
    return segment.replace("-", "_") or None


class ApiError(Exception):
    def __init__(self, status: Optional[int], code: Optional[str], detail: str, request_id: Optional[str] = None):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail
        self.request_id = request_id


class SemanticAnalyticsApiClient:
    def __init__(self, base_url: str, timeout_seconds: float = 30.0, transport: Optional[httpx.BaseTransport] = None):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def _request(self, method: str, path: str, **kw: Any) -> dict:
        try:
            with httpx.Client(base_url=self._base_url, timeout=self._timeout, transport=self._transport) as c:
                r = c.request(method, path, **kw)
        except httpx.HTTPError:
            raise ApiError(None, None, "connection error", None)
        if r.status_code // 100 != 2:
            code = detail = rid = None
            try:
                body = r.json()
                if isinstance(body, dict):
                    code = _code_from_type(body.get("type"))
                    detail, rid = body.get("detail"), body.get("request_id")
            except ValueError:
                pass
            raise ApiError(r.status_code, code, detail or "request failed", rid or r.headers.get("x-request-id"))
        try:
            data = r.json()
        except ValueError:
            raise ApiError(r.status_code, None, "invalid response", None)
        if not isinstance(data, dict):
            raise ApiError(r.status_code, None, "invalid response", None)
        return data

    def ask(self, question: str, conversation_context: List[str], requested_limit: int = 500) -> dict:
        return self._request("POST", "/api/v1/ask", json={
            "question": question,
            "conversation_context": list(conversation_context),
            "authorization_context": {},
            "requested_limit": requested_limit,
        })

    def list_metrics(self) -> dict:
        return self._request("GET", "/api/v1/catalog/metrics")


def result_of(resp: dict) -> Optional[QueryResult]:
    """C-2.9.9: None unless outcome == "result"; a missing or invalid field raises ApiError.

    Accepts the Retriever AskResponse (result nested under "result") or a bare result body.
    """
    if not isinstance(resp, dict) or resp.get("outcome") != "result":
        return None
    rid = resp.get("request_id")
    body = resp.get("result") if isinstance(resp.get("result"), dict) else resp
    try:
        return QueryResult.model_validate({**body, "outcome": "result", "request_id": body.get("request_id", rid)})
    except ValidationError:
        raise ApiError(None, None, "invalid response", rid)


def make_client() -> SemanticAnalyticsApiClient:
    from ..utils import config
    return SemanticAnalyticsApiClient(config.base_url(), config.timeout_seconds(), TRANSPORT_OVERRIDE)
