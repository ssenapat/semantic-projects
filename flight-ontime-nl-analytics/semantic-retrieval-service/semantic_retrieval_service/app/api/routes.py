"""HTTP routes: /api/v1/* and health/metrics (CONTRACT 4.1)."""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from ..schemas.ask import AskRequest, AskResponse, RetrieveRequest, RetrieveResponse
from ..schemas.errors import ApiError
from ..schemas.retrieval import MetricCatalogResponse

router = APIRouter()


def _orch(request: Request):
    return request.app.state.orchestrator


def _require_retriever(request: Request):
    r = _orch(request).retriever
    if r is None:
        detail = getattr(_orch(request), "catalog_error", None) or "The semantic catalog is not loaded."
        raise ApiError("catalog_not_ready", detail, uuid.uuid4().hex)
    return r


@router.post("/api/v1/ask", response_model=AskResponse)
def ask(body: AskRequest, request: Request, response: Response):
    rid = uuid.uuid4().hex
    out = _orch(request).ask(body, rid)
    response.headers["X-Request-ID"] = rid
    return out


@router.post("/api/v1/retrieve", response_model=RetrieveResponse)
def retrieve(body: RetrieveRequest, request: Request, response: Response):
    rid = uuid.uuid4().hex
    out = _orch(request).retrieve(body, rid)
    response.headers["X-Request-ID"] = rid
    return out


@router.get("/api/v1/catalog/status")
def catalog_status(request: Request):
    return _require_retriever(request).status()


@router.get("/api/v1/catalog/metrics", response_model=MetricCatalogResponse)
def catalog_metrics(request: Request):
    return _require_retriever(request).metric_catalog()


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request):
    r = _require_retriever(request)
    return {"status": "ready", "catalog_version": r.status()["catalog_version"]}


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request):
    return PlainTextResponse(request.app.state.metrics.render())
