from __future__ import annotations

import hmac
import logging
import threading
import uuid

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .llm import LLMError, build_llm
from .parser import IntentParser
from .query_client import QueryRefused, QueryServiceClient, UpstreamError
from .schemas import AskRequest
from .settings import Settings

log = logging.getLogger("intent_parser")


class _Unauthorized(Exception):
    pass


def _error(request: Request, status: int, kind: str, message: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"error": {"type": kind, "message": message,
                                   "request_id": getattr(request.state, "request_id", None)}},
                        status_code=status, headers=headers)


def build_parser(settings: Settings) -> IntentParser:
    llm = build_llm(settings)
    queries = QueryServiceClient(settings.query_service_url, settings.query_service_key)
    return IntentParser(llm, queries, settings)


def create_app(parser: IntentParser | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    parser = parser or build_parser(settings)
    slots = threading.BoundedSemaphore(settings.max_concurrent)
    app = FastAPI(title="Pagila intent parser", version="0.1.0", docs_url="/docs", redoc_url=None)
    app.state.parser = parser

    @app.middleware("http")
    async def envelope(request: Request, call_next):
        request.state.request_id = (request.headers.get("x-request-id") or "")[:64] or uuid.uuid4().hex
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return _error(request, 411, "length_required", "a Content-Length header is required")
            if int(length) > settings.max_body_bytes:
                return _error(request, 413, "payload_too_large", f"request body is limited to {settings.max_body_bytes} bytes")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors())
        return _error(request, 422, "invalid_request", detail or "invalid request")

    @app.exception_handler(_Unauthorized)
    async def _unauth(request: Request, exc: _Unauthorized):
        return _error(request, 401, "unauthorized", "a valid X-API-Key header is required", {"WWW-Authenticate": "ApiKey"})

    @app.exception_handler(LLMError)
    async def _llm(request: Request, exc: LLMError):
        log.error("model unavailable request_id=%s: %s", request.state.request_id, exc)
        return _error(request, 502, "llm_unavailable", "the language model could not be reached", {"Retry-After": "5"})

    @app.exception_handler(UpstreamError)
    async def _upstream(request: Request, exc: UpstreamError):
        log.error("query service problem request_id=%s: %s", request.state.request_id, exc)
        return _error(request, 502, "upstream_unavailable", "the query service could not be used", {"Retry-After": "5"})

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unexpected error request_id=%s", request.state.request_id)
        return _error(request, 500, "internal_error", "unexpected error")

    def caller(x_api_key: str | None = Header(default=None)) -> str:
        if not settings.auth_enabled:
            return "anonymous"
        match = None
        for key, name in settings.api_keys.items():
            if x_api_key is not None and hmac.compare_digest(key.encode(), x_api_key.encode()):
                match = name
        if match is None:
            raise _Unauthorized()
        return match

    def check_length(request: Request, body: AskRequest) -> JSONResponse | None:
        if len(body.question) > settings.max_question_chars:
            return _error(request, 422, "question_too_long",
                          f"questions are limited to {settings.max_question_chars} characters")
        return None

    def run(request: Request, body: AskRequest, execute: bool):
        if (too_long := check_length(request, body)) is not None:
            return too_long
        if not slots.acquire(timeout=settings.queue_wait_seconds):
            return JSONResponse({"error": {"type": "busy", "message": "too many questions are being processed; retry shortly",
                                           "request_id": request.state.request_id}},
                                status_code=503, headers={"Retry-After": "2"})
        try:
            rid = request.state.request_id
            parsed = parser.parse(body.question, body.previous_request, rid)
            if not execute or parsed["status"] != "ready":
                return parsed
            try:
                parsed["answer"] = parser.queries.query(parsed["request"], rid)
            except QueryRefused as exc:                    # compiled a moment ago, so this is rare
                parsed.update(status="failed", reason=exc.message)
            return parsed
        finally:
            slots.release()

    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok", "model": settings.model}

    @app.get("/ready", tags=["ops"])
    def ready():
        """The query service answers and has a catalog."""
        parser.queries.catalog()
        return {"status": "ready"}

    @app.post("/v1/parse", tags=["intent"])
    def parse(request: Request, body: AskRequest, _: str = Depends(caller)):
        """Question in, semantic request out. Nothing is executed."""
        return run(request, body, execute=False)

    @app.post("/v1/ask", tags=["intent"])
    def ask(request: Request, body: AskRequest, _: str = Depends(caller)):
        """Question in, semantic request and the query service's answer out."""
        return run(request, body, execute=True)

    return app
