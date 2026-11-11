"""Builds QueryResultResponse: columns, rows, chart hints (CONTRACT §6.6) and query_execution."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal

from ..schemas.query_plan import ValidatedQueryPlan
from ..schemas.result import (ChartHint, ResultColumn, QueryResultMetadata, QueryResultResponse)

_DTYPE = {"date": "date", "timestamp": "date", "text": "string", "string": "string", "integer": "integer",
          "int": "integer", "bigint": "integer", "numeric": "number", "number": "number", "float": "number"}


def _value(v):
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    return v


def build_columns(plan: ValidatedQueryPlan, registry, metrics: list) -> list[ResultColumn]:
    """C-2.5.1: one column per group_by entry (request order), then one per metric (request order)."""
    cols = []
    for g in plan.group_by:
        d = registry.dimension(g["dimension"])
        is_time = d is not None and d.type == "time"
        label = d.label if d is not None else g["dimension"]
        if g.get("time_grain"):
            label = f"{label} ({g['time_grain']})"
        dtype = "date" if is_time else _DTYPE.get((d.data_type or "").lower(), "string") if d else "string"
        cols.append(ResultColumn(name=g["sp1_term"], role="dimension", label=label, unit=None,
                                 semantic_type="time_dimension" if is_time else "dimension", data_type=dtype))
    for metric in metrics:
        cols.append(ResultColumn(name=metric.name, role="metric", label=metric.label or metric.name,
                                 unit=metric.unit, semantic_type="metric",
                                 data_type="integer" if metric.unit == "count" else "number"))
    return cols


def chart_hints(columns: list[ResultColumn], metrics: list, row_count: int) -> list[ChartHint]:
    if len(metrics) > 1:
        return [ChartHint(chart_type="table", confidence=1.0)]
    metric = metrics[0]
    dims = [c for c in columns if c.semantic_type != "metric"]
    times = [c for c in dims if c.semantic_type == "time_dimension"]
    cats = [c for c in dims if c.semantic_type == "dimension"]
    m = metric.name
    primary = None
    if not dims and row_count == 1:
        primary = ChartHint(chart_type="kpi", y_fields=[m], confidence=0.9)
    elif len(dims) == 1 and times:
        primary = ChartHint(chart_type="line", x_field=times[0].name, y_fields=[m], confidence=0.9)
    elif len(dims) == 2 and len(times) == 1 and len(cats) == 1:
        rate = metric.unit != "count"
        primary = ChartHint(chart_type="line" if rate else "stacked_bar", x_field=times[0].name, y_fields=[m],
                            color_field=cats[0].name, confidence=0.9)
    elif len(dims) == 1 and len(cats) == 1:
        primary = ChartHint(chart_type="bar", x_field=cats[0].name, y_fields=[m],
                            title="Top 20" if row_count > 20 else None, confidence=0.9)
    hints = [primary] if primary else []
    hints.append(ChartHint(chart_type="table", confidence=1.0))
    return hints


def format_result(*, plan: ValidatedQueryPlan, registry, metrics: list, raw_rows: list[dict], elapsed_ms: int,
                  effective_limit: int, reference_date: date, explain_checked: bool,
                  max_payload_bytes: int) -> QueryResultResponse:
    columns = build_columns(plan, registry, metrics)
    names = [c.name for c in columns]
    # C-2.5.4 (a): the SQL fetched effective_limit + 1 rows; an extra row means more rows matched.
    truncated = effective_limit > 0 and len(raw_rows) > effective_limit
    if effective_limit > 0:
        raw_rows = raw_rows[:effective_limit]
    rows = [[_value(r.get(n)) for n in names] for r in raw_rows]
    # C-2.5.4 (b): the payload-size cut.
    while rows and len(json.dumps(rows, default=str).encode()) > max_payload_bytes:
        keep = max(0, int(len(rows) * max_payload_bytes / len(json.dumps(rows, default=str).encode())) - 1)
        rows = rows[:min(keep, len(rows) - 1)]
        truncated = True
    dr = plan.resolved_date_range
    meta = QueryResultMetadata(
        metrics=list(plan.metrics), catalog_version=plan.catalog_version,
        time_basis=dr.time_basis if dr else metrics[0].time_basis,
        applied_filters=[{"dimension": f["dimension"], "operator": f["operator"],
                          "values": f["value"] if isinstance(f["value"], list) else [f["value"]]}
                         for f in plan.resolved_filters],
        generated_at_utc=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    qe = {"elapsed_ms": int(elapsed_ms), "effective_limit": int(effective_limit),
          "reference_date": reference_date.isoformat(),
          "resolved_date_range": dr.model_dump() if dr else None,
          "explain_checked": bool(explain_checked), "sql_guard": "passed"}
    return QueryResultResponse(
        outcome="result", request_id=plan.request_id, columns=columns, rows=rows, truncated=truncated,
        row_count=len(rows), metric_versions={m.name: int(m.version) for m in metrics}, metadata=meta,
        chart_hints=chart_hints(columns, metrics, len(rows)), query_execution=qe)
