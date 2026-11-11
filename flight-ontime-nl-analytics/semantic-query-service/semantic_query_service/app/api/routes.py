"""HTTP routes (CONTRACT §4.3)."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from ..schemas.errors import make_envelope

router = APIRouter()


@router.post("/api/v1/query/execute")
async def execute(request: Request):
    svc = request.app.state.service
    rid_header = request.headers.get("x-request-id")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if not isinstance(body, dict):
        svc.metrics.inc("query_service_requests_total")
        svc.metrics.inc("query_service_validation_failures_total")
        env = make_envelope("query_validation_failed", rid_header or "unknown", "The request body is not valid JSON.")
        return JSONResponse(env.model_dump(), status_code=env.status)
    status, payload = svc.execute(body, rid_header)
    return JSONResponse(payload, status_code=status)


@router.get("/health/live")
def live():
    return {"status": "ok"}


@router.get("/health/ready")
def ready(request: Request):
    svc = request.app.state.service
    if svc.ensure_ready():
        return {"status": "ready", "catalog_version": svc.internal.versions.catalog_version,
                "reference_date": svc.reference_date.isoformat(), "value_maps_loaded": True}
    env = make_envelope(svc.not_ready_code, request.headers.get("x-request-id") or "health",
                        svc.not_ready_detail)
    return JSONResponse(env.model_dump(), status_code=env.status)


@router.get("/metrics", response_class=PlainTextResponse)
def metrics(request: Request):
    return request.app.state.service.metrics.render()
