"""Builds a ValidatedQueryPlan or a clarification / unsupported outcome (CONTRACT §7.3, REQ-045..052)."""
from __future__ import annotations

from datetime import date
from typing import Any

from ..repositories.internal_semantic_registry import InternalSemanticRegistry
from ..repositories.mapping_repository import MappingRepository
from ..schemas.query_plan import ValidatedQueryPlan
from ..schemas.semantic_request import (QueryClarificationResponse, QueryExecuteRequest,
                                        QueryUnsupportedResponse)
from . import ambiguity_handler as ah
from . import date_resolver, entity_resolver, status_resolver
from .semantic_request_validator import SemanticRequestValidator, ValidatedParts


def _coerce(dim, value: str) -> Any:
    if dim.data_type == "integer":
        try:
            return int(str(value).strip())
        except ValueError:
            raise ah.Unresolvable(f"'{value}' is not a valid whole number for {dim.label.lower()}.") from None
    if dim.data_type == "date":
        try:
            return date.fromisoformat(str(value).strip()).isoformat()
        except ValueError:
            raise ah.Unresolvable(f"'{value}' is not a valid date (YYYY-MM-DD).",
                                  "unsupported_operation") from None
    return value


def _resolve_filter(f: dict, mappings: MappingRepository) -> dict:
    dim, values = f["dim"], f["values"]
    resolved = entity_resolver.resolve_values(dim, values, mappings)
    if resolved is None:
        resolved = status_resolver.resolve_values(dim, values)
    if resolved is None:
        resolved = values
    typed = [_coerce(dim, v) for v in resolved]
    op = f["sp1_operator"]
    value: Any = typed if op in ("in", "not_in", "between") else typed[0]
    return {"dimension": f["dimension"], "operator": op, "value": value}


def build_plan(req: QueryExecuteRequest, registry: InternalSemanticRegistry, mappings: MappingRepository,
               reference_date: date) -> ValidatedQueryPlan | QueryClarificationResponse | QueryUnsupportedResponse:
    parts = SemanticRequestValidator(registry).validate(req)
    if not isinstance(parts, ValidatedParts):
        return parts
    sr = req.semantic_request
    qp = registry.query_policy
    try:
        resolved_filters = [_resolve_filter(f, mappings) for f in parts.filters]
        drange = None
        mandatory: list[str] = []
        if sr.date_range is not None:
            try:
                drange = date_resolver.resolve(sr.date_range, reference_date, parts.metrics[0].time_basis or "")
            except date_resolver.InvalidDateRange as exc:
                raise ah.Unresolvable(f"Invalid date range: {exc}", "unsupported_operation") from None
        if qp.require_date_filter:
            mandatory.append("date_range")
    except (ah.NeedsClarification, ah.Unresolvable) as exc:
        return ah.from_exception(req.request_id, exc)

    return ValidatedQueryPlan(
        request_id=req.request_id,
        catalog_version=req.catalog_version,
        metrics=[m.name for m in parts.metrics],
        metric_versions={m.name: int(m.version) for m in parts.metrics},
        source_model=parts.model.name,
        group_by=[{"dimension": g["dimension"], "time_grain": g["time_grain"], "sp1_term": g["sp1_term"]}
                  for g in parts.group_by],
        resolved_filters=resolved_filters,
        resolved_date_range=drange,
        mandatory_filters=mandatory,
        requested_limit=min(sr.requested_limit, qp.max_result_rows),
    )
