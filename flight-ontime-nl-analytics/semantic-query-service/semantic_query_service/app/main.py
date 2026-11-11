"""FastAPI app: uvicorn semantic_query_service.app.main:app --host $QUERY_HOST --port $QUERY_PORT."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import __version__
from .api.routes import router
from .api.service import QueryService
from .config import Settings
from .schemas.errors import make_envelope


def create_app(settings: Settings | None = None, executor=None) -> FastAPI:
    settings = settings or Settings()
    service = QueryService(settings, executor)

    @asynccontextmanager
    async def lifespan(_app):
        service.ensure_ready()   # best effort; readiness is retried on /health/ready and on requests
        yield

    app = FastAPI(title="Semantic Query Service", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.include_router(router)

    @app.exception_handler(RequestValidationError)
    async def _validation(_request, _exc):
        env = make_envelope("query_validation_failed", "unknown", "The request failed validation.")
        return JSONResponse(env.model_dump(), status_code=env.status)

    return app


app = create_app()
