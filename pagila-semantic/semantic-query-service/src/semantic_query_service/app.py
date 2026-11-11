"""FastAPI application. Consumers send semantic requests, never SQL (spec section 5)."""
import hmac
import json
import logging
import threading
import uuid

import psycopg
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from semantic_layer import (DefinitionError, GuardError, QueryTimeoutError, RequestError, SemanticError,
                            SemanticLayer, UnsupportedRequest)

from .schemas import request_model
from .settings import Settings

log = logging.getLogger("semantic_query_service")


def _error(request: Request, status: int, kind: str, message: str, headers: dict | None = None) -> JSONResponse:
    rid = getattr(request.state, "request_id", None)
    return JSONResponse({"error": {"type": kind, "message": message, "request_id": rid}},
                        status_code=status, headers=headers)


def create_app(layer: SemanticLayer | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    layer = layer or SemanticLayer(allow_draft=settings.allow_draft)   # bad definitions stop the service at start-up
    Body = request_model(layer.policy())        # list limits come from the policy, not literals
    slots = threading.BoundedSemaphore(settings.max_concurrent_queries)

    app = FastAPI(title="Pagila semantic query service", version="0.1.0",
                  docs_url="/docs", redoc_url=None)
    app.state.layer = layer
    app.state.settings = settings

    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST"],
                           allow_headers=["X-API-Key", "Content-Type", "X-Request-ID"])

    # ---------------------------------------------------------------- middleware
    @app.middleware("http")
    async def envelope(request: Request, call_next):
        request.state.request_id = (request.headers.get("x-request-id") or "")[:64] or uuid.uuid4().hex
        if request.method == "POST":
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return _error(request, 411, "length_required", "a Content-Length header is required")
            if int(length) > settings.max_body_bytes:
                return _error(request, 413, "payload_too_large",
                              f"request body is limited to {settings.max_body_bytes} bytes")
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    # ---------------------------------------------------------------- errors
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        # Only location and message: never echo the submitted values back.
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'] if p != 'body')}: {e['msg']}" for e in exc.errors())
        return _error(request, 422, "invalid_request", detail or "invalid request")

    @app.exception_handler(RequestError)
    async def _request_error(request: Request, exc: RequestError):
        return _error(request, 422, "request_not_allowed", str(exc))

    @app.exception_handler(UnsupportedRequest)
    async def _unsupported(request: Request, exc: UnsupportedRequest):
        rid = getattr(request.state, "request_id", None)
        return JSONResponse({"error": {"type": "unsupported", "message": str(exc), "request_id": rid,
                                       "code": exc.code, "details": exc.details}}, status_code=422)

    @app.exception_handler(QueryTimeoutError)
    async def _timeout(request: Request, exc: QueryTimeoutError):
        return _error(request, 504, "query_timeout", str(exc))

    @app.exception_handler(GuardError)
    async def _guard(request: Request, exc: GuardError):
        # The compiler produced SQL that the guard refused: a bug or an attack, never the caller's fault.
        log.error("guard rejected compiled SQL request_id=%s: %s", request.state.request_id, exc)
        return _error(request, 500, "internal_error", "the query could not be built safely")

    @app.exception_handler(DefinitionError)
    async def _definition(request: Request, exc: DefinitionError):
        log.error("definition error request_id=%s: %s", request.state.request_id, exc)
        return _error(request, 500, "internal_error", "the semantic definitions are inconsistent")

    @app.exception_handler(SemanticError)
    async def _semantic(request: Request, exc: SemanticError):
        log.error("semantic error request_id=%s: %s", request.state.request_id, exc)
        return _error(request, 503, "unavailable", "the semantic layer is not available")

    @app.exception_handler(psycopg.OperationalError)
    async def _db_down(request: Request, exc: psycopg.OperationalError):
        log.error("database unavailable request_id=%s: %s", request.state.request_id, exc)
        return _error(request, 503, "database_unavailable", "the database is not reachable", {"Retry-After": "5"})

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unexpected error request_id=%s", request.state.request_id)
        return _error(request, 500, "internal_error", "unexpected error")

    # ---------------------------------------------------------------- auth
    def caller(request: Request, x_api_key: str | None = Header(default=None)) -> str:
        if not settings.auth_enabled:
            return "anonymous"
        match = None
        for key, name in settings.api_keys.items():            # compare against every key: no early exit
            if x_api_key is not None and hmac.compare_digest(key.encode(), x_api_key.encode()):
                match = name
        if match is None:
            raise _Unauthorized()
        return match

    class _Unauthorized(Exception):
        pass

    @app.exception_handler(_Unauthorized)
    async def _unauth(request: Request, exc: _Unauthorized):
        return _error(request, 401, "unauthorized", "a valid X-API-Key header is required",
                      {"WWW-Authenticate": "ApiKey"})

    # ---------------------------------------------------------------- endpoints
    @app.get("/health", tags=["ops"])
    def health():
        """Liveness: the process is up and the definitions loaded."""
        return {"status": "ok", "models": sorted(layer.catalog())}

    @app.get("/ready", tags=["ops"])
    def ready(request: Request):
        """Readiness: the database answers."""
        layer.execute_sql("SELECT rental_id FROM public.rental LIMIT 1", [])
        return {"status": "ready"}

    @app.get("/v1/catalog", tags=["semantic"])
    def catalog(_: str = Depends(caller)):
        """Models, measures and dimensions a consumer may ask for."""
        return layer.catalog()

    @app.get("/v1/context", tags=["semantic"])
    def context(_: str = Depends(caller)):
        """Facts a consumer needs to resolve relative dates and limits: last date with data, query policy."""
        return {"data_end": layer.data_end(),
                "models": {n: m["version"] for n, m in layer.catalog().items()},
                "policy": layer.policy()}

    @app.post("/v1/compile", tags=["semantic"])
    def compile_request(body: Body, _: str = Depends(caller)):
        """Validate a request and show the SQL it would run. Does not touch the database."""
        c = layer.compile(body.to_layer())
        return {"model": c.model.name, "sql": c.sql, "params": c.params, "limit": c.limit,
                "definitions_used": c.definitions_used, "caveats": c.caveats, "caveat_codes": c.caveat_codes,
                "columns": c.column_meta, "measure_versions": c.measure_versions}

    @app.post("/v1/query", tags=["semantic"])
    def query(body: Body, who: str = Depends(caller)):
        """Run a semantic request. The answer carries its SQL, definition versions and every caveat."""
        if not slots.acquire(timeout=settings.queue_wait_seconds):
            return JSONResponse({"error": {"type": "busy", "message": "too many queries are running; retry shortly"}},
                                status_code=503, headers={"Retry-After": "2"})
        try:
            return layer.query(body.to_layer(), caller=who)
        finally:
            slots.release()

    @app.get("/v1/validation", tags=["semantic"])
    def validation(request: Request, _: str = Depends(caller)):
        """The most recent validation and reconciliation report."""
        path = layer.root / "validation" / "results" / "latest.json"
        if not path.is_file():
            return _error(request, 404, "not_found", "no validation report has been written yet")
        return json.loads(path.read_text())

    return app
