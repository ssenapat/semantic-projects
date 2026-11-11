"""Plan -> SP-1 request -> SQL via semantic_layer.compiler.compile_request (REQ-053, CONTRACT §6.3)."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..schemas.query_plan import ValidatedQueryPlan
from ..schemas.semantic_request import (ClarificationDetails, QueryClarificationResponse,
                                        QueryUnsupportedResponse, UnsupportedDetails)
from ..sp1_bridge import ensure_sp1_on_path
from .internal_catalog_loader import InternalCatalog


@dataclass
class CompiledQuery:
    sql: str
    params: dict = field(default_factory=dict)
    effective_limit: int = 0


_REASONS = {
    "metric_not_in_catalog": "no_approved_metric", "metric_not_exposable": "no_approved_metric",
    "dimension_not_in_catalog": "no_supported_dimension", "dimension_not_exposable": "no_supported_dimension",
    "dimension_not_allowed_for_metric": "no_supported_dimension",
    "metrics_not_combinable": "metrics_not_combinable",
}


def _unsupported(rid: str, reason: str, message: str,
                 conflicting_metrics: list[str] | None = None) -> QueryUnsupportedResponse:
    return QueryUnsupportedResponse(outcome="unsupported", request_id=rid, unsupported=UnsupportedDetails(
        message=(message or "The request cannot be answered.")[:500], reason=reason,
        conflicting_metrics=list(conflicting_metrics or [])))


def _time_dimension(plan: ValidatedQueryPlan, registry) -> str:
    """SP-1 filters use the time *dimension* name; the metric's time_basis may be the physical column."""
    tb = plan.resolved_date_range.time_basis
    if registry is None or registry.dimension(tb) is not None:
        return tb
    metric = registry.metric(plan.metrics[0])
    model = registry.model_for(metric) if metric is not None else None
    for d in (model.dimensions if model is not None else ()):
        if d.type == "time" and (d.column == tb or d.name == tb):
            return d.name
    for d in (model.dimensions if model is not None else ()):
        if d.type == "time":
            return d.name
    return tb


def build_sp1_request(plan: ValidatedQueryPlan, max_rows: int, registry=None) -> dict:
    dims = [g["sp1_term"] for g in plan.group_by]
    filters = [{"dimension": f["dimension"], "operator": f["operator"], "value": f["value"]}
               for f in plan.resolved_filters]
    if plan.resolved_date_range is not None:
        tb = _time_dimension(plan, registry)
        filters.append({"dimension": tb, "operator": "gte", "value": plan.resolved_date_range.start_inclusive})
        filters.append({"dimension": tb, "operator": "lt", "value": plan.resolved_date_range.end_exclusive})
    # C-4.2: no explicit order_by; the compiler orders by metrics[0] DESC NULLS LAST, then dimensions ASC.
    return {"request_id": plan.request_id, "metrics": list(plan.metrics), "dimensions": dims, "filters": filters,
            "order_by": [], "limit": min(plan.requested_limit, max_rows)}


def compile_plan(plan: ValidatedQueryPlan, internal: InternalCatalog, audience: str
                 ) -> CompiledQuery | QueryUnsupportedResponse | QueryClarificationResponse:
    ensure_sp1_on_path()
    from semantic_layer.compiler import compile_request
    for g in plan.group_by:
        if g.get("time_grain") in ("quarter", "year"):
            return _unsupported(plan.request_id, "unsupported_operation",
                                f"time grain '{g['time_grain']}' is not supported")
    max_rows = internal.catalog.query_policy.max_result_rows
    from ..repositories.internal_semantic_registry import InternalSemanticRegistry
    req = build_sp1_request(plan, max_rows, InternalSemanticRegistry(internal, audience))
    resp = compile_request(req, internal.catalog, audience=audience)
    if resp.status == "ok" and resp.sql:
        return CompiledQuery(sql=resp.sql, params=dict(resp.params or {}), effective_limit=int(resp.metadata.get("limit", req["limit"])))
    if resp.status == "clarification_required":
        return QueryClarificationResponse(outcome="clarification_required", request_id=plan.request_id, clarification=ClarificationDetails(
            question=(resp.message or "Please clarify your question.")[:500], ambiguity_type="scope",
            candidate_options=[str(o) for o in (resp.options or [])][:10]))
    if resp.status == "unsupported":
        reason = _REASONS.get(resp.reason_code, "insufficient_context")
    else:
        reason = "unsupported_operation"
    conflicting = list(plan.metrics) if reason == "metrics_not_combinable" else []
    return _unsupported(plan.request_id, reason, resp.message, conflicting)
