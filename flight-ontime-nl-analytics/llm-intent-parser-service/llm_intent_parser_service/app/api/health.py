import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..config import not_ready_detail
from ..errors import envelope
from ..observability.logging import log_event

router = APIRouter()


@router.get("/health/live")
def live() -> dict:
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request):
    """Never calls the LLM: ready iff the client is configured (anthropic needs key and model)."""
    if request.app.state.client is None:
        detail = not_ready_detail(request.app.state.settings)
        log_event(event="not_ready", request_id=request.state.request_id, level=logging.WARNING,
                  error_code="parser_not_ready")
        env = envelope("parser_not_ready", detail, request.state.request_id)
        return JSONResponse(env.model_dump(), status_code=env.status)
    return {"status": "ready"}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request) -> str:
    return request.app.state.metrics.render()
