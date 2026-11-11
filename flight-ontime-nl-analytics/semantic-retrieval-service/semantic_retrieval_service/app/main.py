"""FastAPI application factory for the Semantic Retrieval Service."""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .api.routes import router
from .config import Settings
from .observability.logging_config import SERVICE_VERSION, configure_logging, log_event
from .observability.metrics import Metrics
from .schemas.errors import ApiError
from .services.orchestrator import Orchestrator
from .services.parser_client import ParserClient
from .services.query_service_client import QueryServiceClient
from .services.rate_limiter import RateLimiter


def _load_retriever(settings: Settings, orchestrator=None):
    try:
        from .services import catalog_loader
        from .services.context_retriever import ContextRetriever
        loaded = catalog_loader.load(settings.semantic_layer_definitions_dir, settings.semantic_audience)
        return ContextRetriever(loaded, max_context_tokens=settings.retriever_max_context_tokens,
                                vector_enabled=settings.retriever_vector_enabled,
                                audience=settings.semantic_audience)
    except Exception as exc:  # process stays up; /health/ready answers 503 catalog_not_ready
        from .services.catalog_loader import CatalogLoadError
        detail = str(exc) if isinstance(exc, CatalogLoadError) else f"catalog load failed: {type(exc).__name__}"
        log_event("catalog_load_failed", level="ERROR", outcome="error", error_code=type(exc).__name__)
        log_event("not_ready", level="ERROR", outcome="error", error_code="catalog_not_ready")
        if orchestrator is not None:
            orchestrator.catalog_error = detail
        return None


def _error_response(exc: ApiError) -> JSONResponse:
    return JSONResponse(exc.envelope().model_dump(), status_code=exc.status,
                        headers={"X-Request-ID": exc.request_id})


def create_app(settings: Settings | None = None, *, retriever=None, parser_client=None,
               query_client=None, load_catalog: bool | None = None) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    if load_catalog is None:
        load_catalog = retriever is None
    metrics = Metrics()
    orchestrator = Orchestrator(
        retriever,
        parser_client or ParserClient(settings.parser_base_url, settings.retriever_parser_timeout_seconds),
        query_client or QueryServiceClient(settings.query_service_base_url, settings.retriever_query_timeout_seconds),
        RateLimiter(settings.retriever_rate_limit_per_minute), metrics)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if load_catalog and orchestrator.retriever is None:
            orchestrator.retriever = _load_retriever(settings, orchestrator)
        yield

    app = FastAPI(title="Semantic Retrieval Service", version=SERVICE_VERSION, lifespan=lifespan)
    app.state.orchestrator = orchestrator
    app.state.metrics = metrics
    app.state.settings = settings

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        if not exc.request_id:
            exc.request_id = uuid.uuid4().hex
        return _error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        # Field names only; never echo submitted values (REQ-009).
        fields = sorted({".".join(str(p) for p in e["loc"][1:]) or "body" for e in exc.errors()})
        return _error_response(ApiError("invalid_request", "Invalid request fields: " + ", ".join(fields),
                                        uuid.uuid4().hex))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception):
        logging.getLogger("semantic_retrieval_service").error("", extra={"ghe_fields": {"event": "unhandled"}})
        return JSONResponse({"type": "https://example.internal/errors/internal-error", "title": "Internal error",
                             "status": 500, "detail": "An internal error occurred.",
                             "request_id": uuid.uuid4().hex}, status_code=500)

    app.include_router(router)
    return app


app = create_app()
