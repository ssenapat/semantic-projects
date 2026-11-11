"""Query compiler and audit logger (CONTRACT C-2, C-5, C-6; story S4).

``compile_request`` turns a structured JSON request into parameterised, read-only SQL using
only definitions from the catalog. User input never reaches the SQL text: values are bound
parameters, identifiers come from definitions and are always double-quoted.

CLI: ``python -m semantic_layer.compiler --request PATH|- [--definitions DIR] [--audience A]
[--execute --connection env] [--audit-log PATH]``. Exit codes: 0 ok, 1 non-ok status,
2 usage/definition error, 3 config/database error.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import sys
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .errors import ConfigError, DatabaseError, DefinitionError, PolicyViolation
from .loader import load_catalog
from .models import Catalog, Dimension, Measure, Metric, SemanticModel
from .pg_client import ConnectionSettings, ReadOnlyConnection, connect
from .policy import assert_read_only_sql, check_physical_object, normalize_term

AUDIT_LOGGER_NAME = "semantic_layer.audit"
AUDIENCES = ("consumer", "internal", "reconciliation")
OPERATORS = ("eq", "neq", "in", "not_in", "between", "gt", "gte", "lt", "lte")
TIME_OPERATORS = ("eq", "in", "between", "gt", "gte", "lt", "lte")
CATEGORICAL_OPERATORS = ("eq", "neq", "in", "not_in")
SQL_OPERATORS = {"eq": "=", "neq": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
REQUEST_KEYS = ("request_id", "metric", "metrics", "dimensions", "filters", "order_by", "limit")
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Join aliases and their fixed emission order (C-2.4): c, ao, ad.
JOIN_ALIASES = {
    "flight_to_carrier": "c",
    "flight_to_origin_airport": "ao",
    "flight_to_destination_airport": "ad",
}
MAX_ORDER_BY = 10


# --------------------------------------------------------------------------- data classes
def _json_value(value: Any) -> Any:
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    return value


@dataclass
class CompileResponse:
    status: str
    reason_code: str | None
    message: str
    options: list[str]
    sql: str | None
    params: dict | None
    metadata: dict

    def to_dict(self) -> dict:
        """JSON-ready form (C-2.2): dates become ISO strings, lists stay arrays."""
        return {
            "status": self.status,
            "reason_code": self.reason_code,
            "message": self.message,
            "options": list(self.options),
            "sql": self.sql,
            "params": None if self.params is None else _json_value(self.params),
            "metadata": _json_value(self.metadata),
        }


@dataclass(frozen=True)
class ExecutionResult:
    columns: list[str]
    rows: list[dict]
    row_count: int
    duration_ms: int
    truncated: bool = False


class _Stop(Exception):
    """Internal: a decision-order step produced a non-ok response."""

    def __init__(self, status: str, reason_code: str, message: str, options: list[str] | None = None):
        super().__init__(message)
        self.status = status
        self.reason_code = reason_code
        self.message = message
        self.options = options or []


# --------------------------------------------------------------------------- helpers
def _q(identifier: str) -> str:
    return '"' + str(identifier).replace('"', '""') + '"'


def _qualified(physical_object: str) -> str:
    return ".".join(_q(part) for part in physical_object.split("."))


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _split_grain(term: str) -> tuple[str, str | None]:
    """'flight_date__week' -> ('flight_date', 'week'); no suffix -> (normalized, None)."""
    if "__" in term:
        base, grain = term.rsplit("__", 1)
        return normalize_term(base), normalize_term(grain)
    return normalize_term(term), None


def _canonical(base: str, grain: str | None) -> str:
    return base if grain is None else f"{base}__{grain}"


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _audit_logger(logger: logging.Logger | None) -> logging.Logger:
    return logger if logger is not None else logging.getLogger(AUDIT_LOGGER_NAME)


def _emit_audit(logger: logging.Logger, record: dict) -> None:
    logger.info(json.dumps(record, sort_keys=True, default=str))


def _requested_summary(request: Any) -> tuple[list[str], list[str], list[dict]]:
    """Best-effort as-requested metrics / dimensions / filters for the audit record."""
    metrics: list[str] = []
    dims: list[str] = []
    filters: list[dict] = []
    if isinstance(request, dict):
        m = request.get("metrics")
        if isinstance(m, list):
            metrics = [x for x in m if isinstance(x, str)]
        elif isinstance(request.get("metric"), str):
            metrics = [request["metric"]]
        d = request.get("dimensions")
        if isinstance(d, list):
            dims = [x for x in d if isinstance(x, str)]
        f = request.get("filters")
        if isinstance(f, list):
            for item in f:
                if isinstance(item, dict):
                    filters.append({k: item.get(k) for k in ("dimension", "operator", "value")})
    return metrics, dims, filters


# --------------------------------------------------------------------------- step 1: shape
def _validate_shape(request: Any, catalog: Catalog) -> None:
    qp = catalog.query_policy

    def fail(msg: str) -> None:
        raise _Stop("invalid_request", "schema_error", msg)

    if not isinstance(request, dict):
        fail("request must be a JSON object")
    unknown = sorted(str(k) for k in request if k not in REQUEST_KEYS)
    if unknown:
        fail(f"unknown request keys: {', '.join(unknown)}")
    rid = request.get("request_id")
    if "request_id" in request and not (isinstance(rid, str) and REQUEST_ID_RE.match(rid)):
        fail("request_id must match ^[A-Za-z0-9_-]{1,64}$")
    has_metric, has_metrics = "metric" in request, "metrics" in request
    if has_metric == has_metrics:
        fail("exactly one of 'metric' or 'metrics' is required")
    if has_metric and not isinstance(request["metric"], str):
        fail("'metric' must be a string")
    if has_metrics:
        m = request["metrics"]
        if not isinstance(m, list) or not 1 <= len(m) <= qp.max_metrics_per_query or not all(isinstance(x, str) for x in m):
            fail(f"'metrics' must be a list of 1..{qp.max_metrics_per_query} strings")
        if len(set(m)) != len(m):
            fail("'metrics' items must be unique")
    if "dimensions" in request:
        d = request["dimensions"]
        if not isinstance(d, list) or len(d) > qp.max_dimensions or not all(isinstance(x, str) for x in d):
            fail(f"'dimensions' must be a list of at most {qp.max_dimensions} strings")
        if len(set(d)) != len(d):
            fail("'dimensions' items must be unique")
    if "filters" in request:
        f = request["filters"]
        if not isinstance(f, list) or len(f) > qp.max_filters:
            fail(f"'filters' must be a list of at most {qp.max_filters} objects")
        for item in f:
            if not isinstance(item, dict) or set(item) != {"dimension", "operator", "value"}:
                fail("each filter needs exactly dimension, operator and value")
            if not isinstance(item["dimension"], str):
                fail("filter 'dimension' must be a string")
            if not isinstance(item["operator"], str) or item["operator"] not in OPERATORS:
                fail("filter 'operator' must be one of " + ", ".join(OPERATORS))
    if "order_by" in request:
        o = request["order_by"]
        if not isinstance(o, list) or len(o) > MAX_ORDER_BY:
            fail(f"'order_by' must be a list of at most {MAX_ORDER_BY} objects")
        for item in o:
            if not isinstance(item, dict) or "field" not in item or not set(item) <= {"field", "direction"}:
                fail("each order_by item needs 'field' and optionally 'direction'")
            if not isinstance(item["field"], str):
                fail("order_by 'field' must be a string")
            if "direction" in item and item["direction"] not in ("asc", "desc"):
                fail("order_by 'direction' must be asc or desc")
    if "limit" in request:
        lim = request["limit"]
        if isinstance(lim, float) and lim.is_integer():
            lim = int(lim)
        if not _is_int(lim):
            fail("'limit' must be an integer")


# --------------------------------------------------------------------------- filter values
def _scalar(dim: Dimension, value: Any) -> Any:
    """Typed scalar for a dimension (C-2.1 value rules); ValueError when wrong."""
    if dim.data_type == "date":
        if not isinstance(value, str) or not ISO_DATE_RE.match(value):
            raise ValueError("expected an ISO YYYY-MM-DD date string")
        return dt.date.fromisoformat(value)
    if dim.data_type == "integer":
        if not _is_int(value):
            raise ValueError("expected an integer")
        return value
    if not isinstance(value, str) or "\x00" in value:
        raise ValueError("expected a string")
    return value


def _filter_param(dim: Dimension, op: str, value: Any, max_values: int) -> Any:
    if op in ("in", "not_in"):
        if not isinstance(value, list) or not 1 <= len(value) <= max_values:
            raise ValueError(f"expected a list of 1..{max_values} values")
        return [_scalar(dim, v) for v in value]
    if op == "between":
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError("expected a list of exactly 2 values")
        return [_scalar(dim, v) for v in value]
    if isinstance(value, (list, dict)) or value is None:
        raise ValueError("expected a scalar value")
    return _scalar(dim, value)


# --------------------------------------------------------------------------- SQL rendering
class _Renderer:
    def __init__(self, catalog: Catalog, model: SemanticModel):
        self.catalog = catalog
        self.model = model
        self.fact = catalog.sources[model.primary_source]
        self.joins: dict[str, tuple[str, str, str, str]] = {}  # alias -> (object, right_col, left_col, rel)

    def alias_for(self, dim: Dimension) -> str:
        if dim.source == self.model.primary_source:
            return "f"
        rel = None
        for r in self.catalog.relationships.values():
            if r.left.split(".", 1)[0] != self.model.primary_source:
                continue
            if r.right.split(".", 1)[0] != dim.source:
                continue
            if dim.role is not None and r.role != dim.role:
                continue
            rel = r
            break
        if rel is None:
            raise PolicyViolation("join_not_declared", f"no declared relationship for source {dim.source!r}")
        alias = JOIN_ALIASES.get(rel.name)
        if alias is None:
            raise PolicyViolation("join_not_supported", f"relationship {rel.name!r} has no join alias")
        if self.catalog.fact_to_fact_joins == "prohibited" and rel.right.split(".", 1)[0] == self.model.primary_source:
            raise PolicyViolation("join_prohibited", f"fact-to-fact join {rel.name!r}")
        right_source, right_col = rel.right.split(".", 1)
        left_col = rel.left.split(".", 1)[1]
        for pj in self.catalog.prohibited_joins:
            if pj.source == right_source and pj.column == right_col:
                raise PolicyViolation("join_prohibited", f"join on {pj.source}.{pj.column} is prohibited")
        self.joins[alias] = (self.catalog.sources[right_source].physical_object, right_col, left_col, rel.name)
        return alias

    def column(self, dim: Dimension) -> str:
        return f"{self.alias_for(dim)}.{_q(dim.column)}"

    def select_expr(self, dim: Dimension, grain: str | None) -> str:
        alias = self.alias_for(dim)
        if dim.type == "time" and grain in ("week", "month"):
            return f"DATE_TRUNC('{grain}', {alias}.{_q(dim.column)})::date"
        if dim.null_policy == "label_with_city_name":
            return f'COALESCE({alias}.{_q(dim.column)}::text, {alias}."city_name")'
        return f"{alias}.{_q(dim.column)}"

    def condition(self, column: str, operator: str, value: Any) -> str:
        if operator == "eq" and _is_int(value):
            return f"f.{_q(column)} = {value}"
        if operator == "in" and isinstance(value, (tuple, list)) and value and all(_is_int(v) for v in value):
            return f"f.{_q(column)} IN ({', '.join(str(v) for v in value)})"
        raise DefinitionError("measures", column, f"unsupported measure filter {operator!r} {value!r}")

    def measure_expr(self, name: str | None, metric: Metric) -> str:
        measure: Measure | None = self.model.measure(name or "")
        if measure is None:
            raise DefinitionError("metrics", metric.name, f"unknown measure {name!r}")
        if measure.aggregation == "count":
            inner = "*" if measure.expression == "*" else f"f.{_q(measure.expression)}"
            func = "COUNT"
        elif measure.aggregation == "sum" and measure.expression != "*":
            inner, func = f"f.{_q(measure.expression)}", "SUM"
        else:
            raise DefinitionError("measures", measure.name, "invalid aggregation/expression")
        conds = []
        if measure.filter is not None:
            conds.append(self.condition(measure.filter.column, measure.filter.operator, measure.filter.value))
        for mf in metric.filters:
            conds.append(self.condition(mf.column, mf.operator, mf.value))
        expr = f"{func}({inner})"
        if conds:
            expr += f" FILTER (WHERE {' AND '.join(conds)})"
        return expr

    def metric_expr(self, metric: Metric) -> str:
        if metric.type == "simple":
            return self.measure_expr(metric.measure, metric)
        if metric.type == "ratio":
            num = self.measure_expr(metric.numerator, metric)
            den = self.measure_expr(metric.denominator, metric)
            return f"{num}::numeric / NULLIF({den}, 0)"
        raise DefinitionError("metrics", metric.name, f"unsupported metric type {metric.type!r}")


# --------------------------------------------------------------------------- core compile
def _compile(request: Any, catalog: Catalog, audience: str, request_id: str) -> CompileResponse:
    qp = catalog.query_policy
    _validate_shape(request, catalog)

    metric_terms = [normalize_term(m) for m in (request["metrics"] if "metrics" in request else [request["metric"]])]
    dim_terms = [_split_grain(d) for d in request.get("dimensions", [])]
    filters = request.get("filters", [])
    filter_terms = [_split_grain(f["dimension"]) for f in filters]
    order_items = request.get("order_by", [])
    order_terms = [_split_grain(o["field"]) for o in order_items]

    metric_outs = metric_terms
    dim_outs = [_canonical(b, g) for b, g in dim_terms]
    all_outs = metric_outs + dim_outs
    if len(set(all_outs)) != len(all_outs):
        raise _Stop("invalid_request", "schema_error", "metrics and dimensions must be distinct after normalization")

    terms = list(metric_terms) + [b for b, _ in dim_terms] + [b for b, _ in filter_terms] + [b for b, _ in order_terms]

    # 2. unavailable concepts
    for concept in qp.unavailable_concepts:
        concept_terms = {normalize_term(t) for t in concept.terms}
        hit = next((t for t in terms if t in concept_terms), None)
        if hit is not None:
            raise _Stop("unsupported", concept.reason_code, concept.reason)
    # 3. ambiguous terms
    for amb in qp.ambiguous_terms:
        amb_terms = {normalize_term(t) for t in amb.terms}
        hit = next((t for t in terms if t in amb_terms), None)
        if hit is not None:
            raise _Stop(
                "clarification_required", amb.reason_code,
                f"'{hit}' is ambiguous; choose one of the options", list(amb.options),
            )
    # 4. metrics in catalog
    for name in metric_terms:
        if name not in catalog.metrics:
            raise _Stop("unsupported", "metric_not_in_catalog", f"metric '{name}' is not in the catalog")
    metrics = [catalog.metrics[n] for n in metric_terms]
    shapes = {(m.model, m.grain, m.time_basis, m.combinable_with) for m in metrics}
    if len(metrics) > 1 and (len(shapes) != 1 or any(m.combinable_with is None for m in metrics)):
        raise _Stop("unsupported", "metrics_not_combinable",
                    "metrics cannot be combined: " + ", ".join(m.name for m in metrics))
    model = catalog.model_for(metrics[0])

    # 5. dimensions in model
    def lookup(base: str) -> Dimension:
        dim = model.dimension(base)
        if dim is None:
            raise _Stop("unsupported", "dimension_not_in_catalog", f"dimension '{base}' is not in the catalog")
        return dim

    sel_dims = [lookup(b) for b, _ in dim_terms]
    flt_dims = [lookup(b) for b, _ in filter_terms]
    # 6. metric status
    for m in metrics:
        if not catalog.is_exposable(m.status, audience):
            raise _Stop("unsupported", "metric_not_exposable",
                        f"metric '{m.name}' has status '{m.status}' which is not exposable to audience '{audience}'")
    # 7. dimension status
    for d in sel_dims + flt_dims:
        status = d.status or model.status
        if not catalog.is_exposable(status, audience):
            raise _Stop("unsupported", "dimension_not_exposable",
                        f"dimension '{d.name}' has status '{status}' which is not exposable to audience '{audience}'")
    # 8. allowed dimensions
    for m in metrics:
        allowed = set(m.allowed_dimensions)
        for d in sel_dims:
            if d.name not in allowed:
                raise _Stop("unsupported", "dimension_not_allowed_for_metric",
                            f"dimension '{d.name}' is not allowed for metric '{m.name}'")
        for d in flt_dims:
            if d.name not in allowed and d.name != model.time_dimension:
                raise _Stop("unsupported", "dimension_not_allowed_for_metric",
                            f"dimension '{d.name}' is not allowed for metric '{m.name}'")
    # 9. grain
    for d, (_, grain) in zip(sel_dims, dim_terms):
        if grain is not None and (d.type != "time" or grain not in d.granularities):
            raise _Stop("invalid_request", "grain_not_allowed", f"grain '{grain}' is not allowed for '{d.name}'")
    for d, (_, grain) in zip(flt_dims, filter_terms):
        if grain is not None:
            raise _Stop("invalid_request", "grain_not_allowed", "filters use the base dimension name, not a grain suffix")
    # 10. operator vs dimension type
    for d, f in zip(flt_dims, filters):
        allowed_ops = TIME_OPERATORS if d.type == "time" else CATEGORICAL_OPERATORS
        if f["operator"] not in allowed_ops:
            raise _Stop("invalid_request", "operator_not_allowed",
                        f"operator '{f['operator']}' is not allowed for dimension '{d.name}'")
    # 11. values
    filter_params: list[Any] = []
    for d, f in zip(flt_dims, filters):
        try:
            filter_params.append(_filter_param(d, f["operator"], f["value"], qp.max_filter_values))
        except ValueError as exc:
            raise _Stop("invalid_request", "invalid_filter_value", f"filter on '{d.name}': {exc}") from None
    # 12. order_by fields
    for item, (base, grain) in zip(order_items, order_terms):
        if _canonical(base, grain) not in all_outs:
            raise _Stop("invalid_request", "order_by_field_not_selected",
                        f"order_by field '{item['field']}' is not a selected metric or dimension")
    # 13. limit
    warnings: list[str] = []
    limit = request.get("limit", qp.default_limit)
    if isinstance(limit, float):
        limit = int(limit)
    if limit < 1:
        raise _Stop("invalid_request", "invalid_limit", "limit must be at least 1")
    if limit > qp.max_result_rows:
        limit = qp.max_result_rows
        warnings.append("limit_clamped")
    effective_limit = limit

    # 14. render
    r = _Renderer(catalog, model)
    select_items = [
        f'{r.select_expr(d, g)} AS {_q(out)}' for d, (_, g), out in zip(sel_dims, dim_terms, dim_outs)
    ]
    group_items = [r.select_expr(d, g) for d, (_, g) in zip(sel_dims, dim_terms)]
    select_items += [f"{r.metric_expr(m)} AS {_q(m.name)}" for m in metrics]

    params: dict[str, Any] = {}
    where: list[str] = []
    n = 0
    for d, f, value in zip(flt_dims, filters, filter_params):
        col = r.column(d)
        op = f["operator"]
        if op == "between":
            params[f"p{n + 1}"], params[f"p{n + 2}"] = value
            where.append(f"{col} BETWEEN %(p{n + 1})s AND %(p{n + 2})s")
            n += 2
            continue
        n += 1
        params[f"p{n}"] = value
        if op == "in":
            where.append(f"{col} = ANY(%(p{n})s)")
        elif op == "not_in":
            where.append(f"NOT ({col} = ANY(%(p{n})s))")
        else:
            where.append(f"{col} {SQL_OPERATORS[op]} %(p{n})s")
    params["limit"] = effective_limit + 1  # one extra row detects truncation (C-2.8.5)

    order_sql = []
    listed = set()
    for item, (base, grain) in zip(order_items, order_terms):
        out = _canonical(base, grain)
        listed.add(out)
        order_sql.append(f'{_q(out)} {item.get("direction", "asc").upper()}')
    if metrics[0].name not in listed:
        listed.add(metrics[0].name)
        order_sql.append(f'{_q(metrics[0].name)} DESC NULLS LAST')
    order_sql += [f'{_q(out)} ASC' for out in dim_outs if out not in listed]

    lines = ["SELECT " + ", ".join(select_items), f"FROM {_qualified(r.fact.physical_object)} AS f"]
    objects = [r.fact.physical_object]
    for rel_name, alias in JOIN_ALIASES.items():
        if alias in r.joins:
            obj, right_col, left_col, _ = r.joins[alias]
            objects.append(obj)
            lines.append(f"JOIN {_qualified(obj)} AS {alias} ON {alias}.{_q(right_col)} = f.{_q(left_col)}")
    if where:
        lines.append("WHERE " + " AND ".join(where))
    if group_items:
        lines.append("GROUP BY " + ", ".join(group_items))
    if order_sql:
        lines.append("ORDER BY " + ", ".join(order_sql))
    lines.append("LIMIT %(limit)s")
    sql = "\n".join(lines)

    for obj in objects:
        check_physical_object(catalog, obj, "query")
    assert_read_only_sql(sql, qp)

    # metadata
    for m in metrics:
        if m.open_question:
            warnings.append(f"open_question:{m.open_question}")
    for d in sel_dims:
        if d.note and d.note.lower().startswith("constant") or d.name in ("month", "quarter"):
            warnings.append(f"constant_dimension:{d.name}")
        if d.null_policy == "label_with_city_name":
            warnings.append("destination_code_city_fallback")
    value_labels = {
        out: {str(k): v for k, v in d.values.items()}
        for d, out in zip(sel_dims, dim_outs)
        if isinstance(d.values, dict)
    }
    metadata = {
        "request_id": request_id,
        "audience": audience,
        "definitions_version": catalog.definitions_version,
        "model": model.name,
        "metrics": [{"name": m.name, "version": m.version, "status": m.status, "unit": m.unit,
                     "label": m.label} for m in metrics],
        "dimensions": list(dim_outs),
        "filters": [
            {"dimension": d.name, "operator": f["operator"], "value": f["value"]} for d, f in zip(flt_dims, filters)
        ],
        "limit": effective_limit,
        "value_labels": value_labels,
        "warnings": list(dict.fromkeys(warnings)),
    }
    return CompileResponse("ok", None, "ok", [], sql, params, metadata)


def compile_request(
    request: dict, catalog: Catalog, *, audience: str = "internal", logger: logging.Logger | None = None
) -> CompileResponse:
    """Compile a request (C-2). Never raises for bad user input; emits one audit record."""
    if audience not in AUDIENCES:
        raise ValueError(f"unknown audience {audience!r}")
    rid = request.get("request_id") if isinstance(request, dict) else None
    request_id = rid if isinstance(rid, str) and REQUEST_ID_RE.match(rid) else uuid.uuid4().hex
    try:
        response = _compile(request, catalog, audience, request_id)
    except _Stop as stop:
        response = CompileResponse(
            stop.status, stop.reason_code, stop.message, list(stop.options), None, None,
            {"request_id": request_id, "audience": audience, "definitions_version": catalog.definitions_version},
        )
    metrics, dims, filters = _requested_summary(request)
    versions = {}
    for name in metrics:
        metric = catalog.metrics.get(normalize_term(name))
        if metric is not None:
            versions[metric.name] = metric.version
    _emit_audit(_audit_logger(logger), {
        "event": "compile",
        "timestamp": _now_iso(),
        "request_id": request_id,
        "audience": audience,
        "status": response.status,
        "reason_code": response.reason_code,
        "metrics": metrics,
        "dimensions": dims,
        "filters": filters,
        "definitions_version": catalog.definitions_version,
        "metric_versions": versions,
        "row_count": None,
        "duration_ms": None,
    })
    return response


def execute(
    response: CompileResponse, conn: ReadOnlyConnection, *, logger: logging.Logger | None = None
) -> ExecutionResult:
    """Run an ok response through a read-only connection; one audit record (no row data)."""
    if response.status != "ok" or response.sql is None:
        raise ValueError("only responses with status 'ok' can be executed")
    meta = response.metadata
    record = {
        "event": "execute",
        "request_id": meta.get("request_id"),
        "audience": meta.get("audience"),
        "status": response.status,
        "reason_code": response.reason_code,
        "metrics": [m["name"] for m in meta.get("metrics", [])],
        "dimensions": list(meta.get("dimensions", [])),
        "filters": list(meta.get("filters", [])),
        "definitions_version": meta.get("definitions_version"),
        "metric_versions": {m["name"]: m["version"] for m in meta.get("metrics", [])},
    }
    log = _audit_logger(logger)
    try:
        result = conn.fetch(response.sql, response.params)
    except Exception:
        _emit_audit(log, {**record, "timestamp": _now_iso(), "status": "error",
                          "reason_code": "execution_failed", "row_count": None, "duration_ms": None})
        raise
    rows = [dict(zip(result.columns, row)) for row in result.rows]
    limit = meta.get("limit")
    truncated = limit is not None and len(rows) > limit
    if truncated:
        rows = rows[:limit]
    _emit_audit(log, {**record, "timestamp": _now_iso(), "row_count": len(rows),
                      "duration_ms": result.duration_ms})
    return ExecutionResult(list(result.columns), rows, len(rows), result.duration_ms, truncated)


# --------------------------------------------------------------------------- CLI
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m semantic_layer.compiler", description=__doc__.split("\n")[0])
    p.add_argument("--definitions", default="definitions/", help="definitions directory (default definitions/)")
    p.add_argument("--request", required=True, help="request JSON file, or - for stdin")
    p.add_argument("--audience", choices=("internal", "consumer"), default="internal")
    p.add_argument("--execute", action="store_true", help="run the compiled SQL on a read-only connection")
    p.add_argument("--connection", choices=("env",), default="env", help="connection source (env only)")
    p.add_argument("--audit-log", default=None, help="append audit lines to this file instead of stderr")
    return p


def _result_json(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def main(argv: list[str] | None = None) -> int:
    try:
        ns = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 0

    try:
        text = sys.stdin.read() if ns.request == "-" else open(ns.request, encoding="utf-8").read()
        request = json.loads(text)
    except (OSError, ValueError) as exc:
        print(f"error: cannot read request: {exc}", file=sys.stderr)
        return 2
    try:
        catalog = load_catalog(ns.definitions)
    except (DefinitionError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    old_level, old_propagate = logger.level, logger.propagate
    try:
        handler: logging.Handler = (
            logging.FileHandler(ns.audit_log, mode="a", encoding="utf-8") if ns.audit_log
            else logging.StreamHandler(sys.stderr)
        )
    except OSError as exc:
        print(f"error: cannot open audit log: {exc}", file=sys.stderr)
        return 2
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        try:
            response = compile_request(request, catalog, audience=ns.audience, logger=logger)
        except DefinitionError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        out = response.to_dict()
        if response.status != "ok":
            print(json.dumps(out, indent=2))
            return 1
        if ns.execute:
            qp = catalog.query_policy
            try:
                settings = ConnectionSettings.from_env(catalog.connection.database)
                with connect(settings, qp.statement_timeout_seconds,
                             qp.idle_in_transaction_session_timeout_seconds) as conn:
                    result = execute(response, conn, logger=logger)
            except (ConfigError, DatabaseError) as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 3
            out["result"] = {
                "columns": result.columns,
                "rows": json.loads(json.dumps(result.rows, default=_result_json)),
                "row_count": result.row_count,
                "duration_ms": result.duration_ms,
                "truncated": result.truncated,
            }
        print(json.dumps(out, indent=2))
        return 0
    finally:
        logger.removeHandler(handler)
        handler.close()
        logger.setLevel(old_level)
        logger.propagate = old_propagate


if __name__ == "__main__":
    sys.exit(main())
