"""Structural validation of the SP-1 catalog before indexing (REQ-006, REQ-017)."""
from __future__ import annotations

from typing import Any


def validate(catalog: Any, audience: str) -> list[str]:
    """Return a list of error strings (empty list = valid). Messages carry no paths or SQL."""
    from semantic_layer.models import STATUS_ORDER  # SP-1, imported via sp1_bridge

    errors: list[str] = []
    policy = catalog.query_policy
    minimum = policy.audiences.get(audience)
    if minimum is None or minimum not in STATUS_ORDER:
        errors.append(f"unknown semantic audience: {audience!r}")
    if not catalog.models:
        errors.append("catalog has no semantic models")
    if not catalog.metrics:
        errors.append("catalog has no metrics")

    seen: set[str] = set()
    for model in catalog.models.values():
        if model.status not in STATUS_ORDER:
            errors.append(f"model {model.name}: invalid status {model.status!r}")
        names = [d.name for d in model.dimensions]
        for dup in sorted({n for n in names if names.count(n) > 1}):
            errors.append(f"model {model.name}: duplicate dimension {dup}")
        for d in model.dimensions:
            if d.status not in STATUS_ORDER:
                errors.append(f"dimension {d.name}: invalid status {d.status!r}")
        if model.time_dimension and model.dimension(model.time_dimension) is None:
            errors.append(f"model {model.name}: time dimension {model.time_dimension} is not a dimension")

    for metric in catalog.metrics.values():
        if metric.name in seen:
            errors.append(f"duplicate metric {metric.name}")
        seen.add(metric.name)
        if metric.status not in STATUS_ORDER:
            errors.append(f"metric {metric.name}: invalid status {metric.status!r}")
        if not metric.label or not metric.description:
            errors.append(f"metric {metric.name}: label and description are required")
        model = catalog.models.get(metric.model or "")
        if model is None:
            errors.append(f"metric {metric.name}: unknown semantic model {metric.model!r}")
            continue
        for dim in metric.allowed_dimensions or ():
            if model.dimension(dim) is None:
                errors.append(f"metric {metric.name}: allowed dimension {dim} does not exist")

    try:  # reuse the SP-1 policy check when importable
        from semantic_layer.validator import run_policies

        result = run_policies(catalog)
        if result.status == "fail":
            errors.extend(f"policy: {o}" for o in (result.offenders or result.details))
    except ImportError:  # optional DB driver missing: structural checks above still apply
        pass
    return errors
