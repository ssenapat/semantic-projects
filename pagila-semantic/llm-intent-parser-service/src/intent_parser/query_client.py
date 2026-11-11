"""Talks to semantic-query-service. The parser only ever sees what that service exposes."""
from __future__ import annotations

import httpx


class UpstreamError(Exception):
    """The query service could not be used (down, rejected our key, or broke)."""


class QueryRefused(Exception):
    """The query service understood the request and refused it (HTTP 422)."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind, self.message = kind, message


class QueryServiceClient:
    def __init__(self, base_url: str = "", api_key: str | None = None, timeout: float = 40.0,
                 client: httpx.Client | None = None):
        headers = {"X-API-Key": api_key} if api_key else {}
        self._http = client or httpx.Client(base_url=base_url, headers=headers, timeout=timeout)

    def close(self) -> None:
        self._http.close()

    def _call(self, method: str, path: str, request_id: str | None, **kw) -> dict:
        headers = {"X-Request-ID": request_id} if request_id else {}
        try:
            r = self._http.request(method, path, headers=headers, **kw)
        except httpx.HTTPError as exc:
            raise UpstreamError(f"query service unreachable: {type(exc).__name__}") from None
        if r.status_code == 422:
            err = (r.json().get("error") or {}) if r.headers.get("content-type", "").startswith("application/json") else {}
            raise QueryRefused(err.get("type", "invalid_request"), err.get("message", "request refused"))
        if r.status_code != 200:
            raise UpstreamError(f"query service answered {r.status_code}")
        return r.json()

    def catalog(self, request_id: str | None = None) -> dict:
        return self._call("GET", "/v1/catalog", request_id)

    def context(self, request_id: str | None = None) -> dict:
        return self._call("GET", "/v1/context", request_id)

    def compile(self, request: dict, request_id: str | None = None) -> dict:
        return self._call("POST", "/v1/compile", request_id, json=request)

    def query(self, request: dict, request_id: str | None = None) -> dict:
        return self._call("POST", "/v1/query", request_id, json=request)
