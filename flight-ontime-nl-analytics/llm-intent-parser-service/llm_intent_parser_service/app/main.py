import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api import health, routes
from .config import SERVICE_NAME, SERVICE_VERSION, Settings, get_settings
from .errors import ParserError, envelope
from .observability.logging import configure_logging, log_event
from .observability.metrics import Metrics
from .services.llm_client import build_llm_client


def _safe_validation_detail(exc: RequestValidationError) -> tuple[bool, str]:
    """(is_context_error, detail) built from locations only; input values are never echoed."""
    locs = [".".join(str(p) for p in e["loc"] if p != "body") for e in exc.errors()][:5]
    in_ctx = any(e["loc"][:2] == ("body", "semantic_context") and len(e["loc"]) > 2 for e in exc.errors())
    return in_ctx, "Invalid fields: " + (", ".join(locs) if locs else "body")


def create_app(settings: Settings | None = None, llm_client=None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title=SERVICE_NAME, version=SERVICE_VERSION)
    app.state.settings = settings
    app.state.metrics = Metrics()
    app.state.limiter = routes.SlidingWindowLimiter(settings.parser_rate_limit_per_minute)
    app.state.client = llm_client if llm_client is not None else build_llm_client(settings)

    def _error_response(request: Request, code: str, detail: str) -> JSONResponse:
        env = envelope(code, detail, request.state.request_id)
        request.state.error_code = code
        return JSONResponse(env.model_dump(), status_code=env.status)

    @app.exception_handler(ParserError)
    async def _parser_error(request: Request, exc: ParserError):
        return _error_response(request, exc.code, exc.detail)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError):
        in_ctx, detail = _safe_validation_detail(exc)
        return _error_response(request, "invalid_semantic_context" if in_ctx else "invalid_request", detail)

    @app.middleware("http")
    async def _observe(request: Request, call_next):
        t0 = time.perf_counter()
        request.state.request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.catalog_version = request.state.outcome = request.state.error_code = None
        request.state.question = None
        response = await call_next(request)
        latency = (time.perf_counter() - t0) * 1000
        response.headers["X-Request-ID"] = request.state.request_id
        if request.url.path == routes.PARSE_PATH:
            m: Metrics = app.state.metrics
            m.inc("intent_parser_requests_total")
            m.set("intent_parser_latency_ms", latency)
            failed = response.status_code >= 400
            if failed:
                m.inc("intent_parser_failures_total")
            log_event(event="intent_parse", request_id=request.state.request_id,
                      catalog_version=request.state.catalog_version, outcome=request.state.outcome,
                      latency_ms=latency, level=logging.WARNING if failed else logging.INFO,
                      question=request.state.question, error_code=request.state.error_code)
        return response

    app.include_router(health.router)
    app.include_router(routes.router)
    return app


app = create_app()
