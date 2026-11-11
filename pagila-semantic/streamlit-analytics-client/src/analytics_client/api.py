"""HTTP client for llm-intent-parser-service. Turns every failure into an ApiError with a message fit for the screen."""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


class ApiError(Exception):
    def __init__(self, kind: str, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.kind, self.message, self.retry_after = kind, message, retry_after


@dataclass(frozen=True)
class Config:
    base_url: str = "http://127.0.0.1:8001"
    api_key: str | None = None
    timeout: float = 120.0          # a question can take up to three model calls plus the query

    @classmethod
    def from_env(cls) -> "Config":
        return cls(base_url=os.environ.get("ANALYTICS_INTENT_URL", cls.base_url).rstrip("/"),
                   api_key=os.environ.get("ANALYTICS_INTENT_API_KEY") or None,
                   timeout=float(os.environ.get("ANALYTICS_REQUEST_TIMEOUT", cls.timeout)))


class IntentApi:
    def __init__(self, config: Config | None = None, transport: httpx.BaseTransport | None = None):
        self.config = config or Config.from_env()
        self._transport = transport

    def _client(self) -> httpx.Client:
        headers = {"X-API-Key": self.config.api_key} if self.config.api_key else {}
        return httpx.Client(base_url=self.config.base_url, headers=headers, timeout=self.config.timeout,
                            transport=self._transport)

    def _send(self, method: str, path: str, **kw) -> dict:
        try:
            with self._client() as client:
                r = client.request(method, path, **kw)
        except httpx.TimeoutException:
            raise ApiError("timeout", "The question took too long to answer. Try a simpler question.") from None
        except httpx.HTTPError:
            raise ApiError("unreachable", f"Cannot reach the intent service at {self.config.base_url}. Is it running?") from None
        if r.status_code == 200:
            try:
                return r.json()
            except ValueError:
                raise ApiError("bad_response", "The intent service sent an unreadable reply.") from None
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        message = err.get("message") if isinstance(err.get("message"), str) else None
        if r.status_code == 401:
            raise ApiError("unauthorized", "The intent service rejected the API key. Check ANALYTICS_INTENT_API_KEY.")
        if r.status_code == 503:
            retry = r.headers.get("retry-after", "")
            raise ApiError("busy", "The service is busy. Please try again in a moment.",
                           float(retry) if retry.replace(".", "", 1).isdigit() else None)
        if r.status_code in (413, 422):
            raise ApiError("invalid", message or "The question was not accepted.")
        if r.status_code == 502:
            raise ApiError("upstream", message or "The service could not reach the language model or the data service.")
        raise ApiError("server", "The intent service had a problem. Please try again.")

    def ask(self, question: str, previous_request: dict | None = None) -> dict:
        body: dict = {"question": question}
        if previous_request:
            body["previous_request"] = previous_request
        return self._send("POST", "/v1/ask", json=body)

    def ready(self) -> tuple[bool, str]:
        try:
            self._send("GET", "/ready")
            return True, "connected"
        except ApiError as exc:
            return False, exc.message
