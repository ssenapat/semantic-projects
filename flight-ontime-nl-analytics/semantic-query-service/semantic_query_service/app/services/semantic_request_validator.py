"""Validates a ConstrainedSemanticRequest against the internal catalog (REQ-006/045, Spec §24 steps 1-7).

Returns a ``ValidatedParts`` on success or a Query{Unsupported,Clarification}Response (CONTRACT §6.3 reasons).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..repositories.internal_semantic_registry import InternalSemanticRegistry
from ..schemas.semantic_request import (QueryClarificationResponse, QueryExecuteRequest,
                                        QueryUnsupportedResponse)
from . import ambiguity_handler as ah

TIME_OPERATORS = {"equals", "between"}
CATEGORICAL_OPERATORS = {"equals", "not_equals", "in", "not_in"}
SP1_OPERATOR = {"equals": "eq", "not_equals": "neq", "in": "in", "not_in": "not_in", "between": "between"}
SP1_GRAINS_OK = {"day", "week", "month"}


def normalize_term(term: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")


def _shape(metric, model_of) -> tuple | None:
    """Combinability key: None when the metric declares no ``combinable_with`` (never combinable)."""
    if metric.combinable_with is None:
        return None
    return (metric.combinable_with, model_of(metric).name, metric.grain, metric.time_basis)


def _conflicting_metrics(metrics: list, model_of) -> list[str] | None:
    """None when all metrics are combinable, else the conflicting names in request order (C-2.4.1 step 4)."""
    first = _shape(metrics[0], model_of)
    if first is None:
        return [m.name for m in metrics]
    bad = [m.name for m in metrics[1:] if _shape(m, model_of) != first]
    return [metrics[0].name] + bad if bad else None


@dataclass
class ValidatedParts:
    metrics: list[Any]                                    # catalog Metric objects, request order
    model: Any
    group_by: list[dict] = field(default_factory=list)   # {"dimension","time_grain","sp1_term","dim"}
    filters: list[dict] = field(default_factory=list)    # {"dimension","operator","sp1_operator","values","dim"}


Outcome = QueryUnsupportedResponse | QueryClarificationResponse


class SemanticRequestValidator:
    def __init__(self, registry: InternalSemanticRegistry):
        self.registry = registry

    def validate(self, req: QueryExecuteRequest) -> ValidatedParts | Outcome:
        try:
            return self._validate(req)
        except (ah.NeedsClarification, ah.Unresolvable) as exc:
            return ah.from_exception(req.request_id, exc)

    # ------------------------------------------------------------------
    def _validate(self, req: QueryExecuteRequest) -> ValidatedParts:
        sr = req.semantic_request
        reg = self.registry
        qp = reg.query_policy
        self._policy_terms(sr)                                               # step 1

        metrics = []                                                         # step 2
        for name in sr.metrics:
            m = reg.metric(name)
            if m is None:
                raise ah.Unresolvable(f"Metric '{name}' is not an approved metric.", "no_approved_metric")
            metrics.append(m)
        names = [m.name for m in metrics]
        model = reg.model_for(metrics[0])

        if len(metrics) > 1:                                                 # step 3
            conflicting = _conflicting_metrics(metrics, model_of=reg.model_for)
            if conflicting is not None:
                raise ah.Unresolvable(
                    f"These metrics cannot be combined in one query: {', '.join(conflicting)}.",
                    "metrics_not_combinable", conflicting)

        if len(sr.group_by) > qp.max_dimensions:                             # step 4
            raise ah.Unresolvable(f"At most {qp.max_dimensions} group-by dimensions are supported.",
                                  "unsupported_operation")
        if len(sr.filters) > qp.max_filters:
            raise ah.Unresolvable(f"At most {qp.max_filters} filters are supported.", "unsupported_operation")

        group_dims = [self._check_dimension(g.dimension, metrics, model, is_filter=False) for g in sr.group_by]
        filter_dims = [self._check_dimension(f.dimension, metrics, model, is_filter=True) for f in sr.filters]

        parts = ValidatedParts(metrics=metrics, model=model)                 # step 6 rules
        seen: set[str] = set()
        for g, dim in zip(sr.group_by, group_dims):
            grain = g.time_grain
            if grain is not None:
                if dim.type != "time" or grain not in SP1_GRAINS_OK or grain not in dim.granularities:
                    raise ah.Unresolvable(f"Time grain '{grain}' is not supported for '{dim.name}'.",
                                          "unsupported_operation")
            term = dim.name if grain in (None, "day") else f"{dim.name}__{grain}"
            if term in seen:
                raise ah.Unresolvable(f"Dimension '{term}' is requested more than once.",
                                      "unsupported_operation")
            seen.add(term)
            parts.group_by.append({"dimension": dim.name, "time_grain": grain, "sp1_term": term, "dim": dim})

        for f, dim in zip(sr.filters, filter_dims):
            if f.operator == "contains":
                raise ah.Unresolvable("The 'contains' operator is not supported.", "unsupported_operation")
            ops = TIME_OPERATORS if dim.type == "time" else CATEGORICAL_OPERATORS
            if f.operator not in ops:
                raise ah.Unresolvable(f"Operator '{f.operator}' is not supported for '{dim.name}'.",
                                      "unsupported_operation")
            if len(f.values) > qp.max_filter_values:
                raise ah.Unresolvable(f"At most {qp.max_filter_values} filter values are supported.",
                                      "unsupported_operation")
            if f.operator == "between" and len(f.values) != 2:
                raise ah.Unresolvable("A 'between' filter needs exactly two values.", "unsupported_operation")
            if f.operator in ("equals", "not_equals") and len(f.values) != 1:
                raise ah.Unresolvable(f"A '{f.operator}' filter needs exactly one value.",
                                      "unsupported_operation")
            parts.filters.append({"dimension": dim.name, "operator": f.operator,
                                  "sp1_operator": SP1_OPERATOR[f.operator], "values": list(f.values),
                                  "dim": dim})

        if sr.date_range is not None and not model.time_dimension:
            raise ah.Unresolvable("The metrics have no time dimension for a date range."
                                  if len(names) > 1 else "The metric has no time dimension for a date range.",
                                  "unsupported_operation")
        if qp.require_date_filter and sr.date_range is None:
            raise ah.NeedsClarification("Which time period should this cover?", "time_interpretation", [])
        return parts

    def _check_dimension(self, name: str, metrics: list, model, *, is_filter: bool):
        """Step 5: the dimension must be allowed by every metric (filters also accept the time dimension)."""
        dim = self.registry.dimension_for_metric(metrics[0], name)
        blocking = [m.name for m in metrics
                    if dim is None or (dim.name not in set(m.allowed_dimensions or ())
                                       and not (is_filter and dim.name == model.time_dimension))]
        if not blocking:
            return dim
        if len(blocking) == len(metrics):
            what = "cannot be used to filter" if is_filter else "is not available for"
            if len(metrics) == 1:
                msg = f"Dimension '{name}' {what} metric '{metrics[0].name}'."
            else:
                msg = f"Dimension '{name}' {what} metrics " + ", ".join(f"'{m.name}'" for m in metrics) + "."
            raise ah.Unresolvable(msg, "no_supported_dimension", [] if len(metrics) == 1 else blocking)
        raise ah.Unresolvable(f"Dimension '{name}' is not available for: {', '.join(blocking)}.",
                              "no_supported_dimension", blocking)

    def _policy_terms(self, sr) -> None:
        """SP-1 unavailable concepts (→ insufficient_context) and ambiguous terms (→ clarification)."""
        qp = self.registry.query_policy
        terms = [normalize_term(m) for m in sr.metrics]
        terms += [normalize_term(g.dimension) for g in sr.group_by]
        terms += [normalize_term(f.dimension) for f in sr.filters]
        for concept in qp.unavailable_concepts:
            ct = {normalize_term(t) for t in concept.terms}
            if any(t in ct for t in terms):
                raise ah.Unresolvable(concept.reason, "insufficient_context")
        for amb in qp.ambiguous_terms:
            at = {normalize_term(t) for t in amb.terms}
            hit = next((t for t in terms if t in at), None)
            if hit is not None:
                kind = "airport_or_location" if "airport" in amb.reason_code else "metric"
                raise ah.NeedsClarification(f"'{hit}' is ambiguous; which did you mean?", kind,
                                            list(amb.options))
