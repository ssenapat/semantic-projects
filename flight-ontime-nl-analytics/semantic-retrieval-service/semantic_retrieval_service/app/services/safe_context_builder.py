"""Builds LlmSemanticContext from LLM-safe payloads only (REQ-020/021/022)."""
from __future__ import annotations

import json
import math
import re

from ..schemas.catalog import SemanticDocument
from ..schemas.retrieval import (LlmSafeAmbiguityRule, LlmSafeDimension, LlmSafeGlossaryEntry, LlmSafeMetric,
                                 LlmSemanticContext)
from .document_normalizer import DOMAIN_RULES

MAX_METRICS, HARD_MAX_METRICS = 8, 15
MAX_DIMENSIONS = 50
MAX_GLOSSARY, HARD_MAX_GLOSSARY = 15, 30
MAX_RULES = 20

_LEAK = re.compile(r"(?i)\b(ontime|select|insert|delete|join|tran-stats|fl_date|schema)\b|\w+\.\w+\(|[;`]")


class ContextLeakError(ValueError):
    """The assembled context contained SQL/physical-name text."""


def estimate_tokens(context: LlmSemanticContext) -> int:
    return math.ceil(len(json.dumps(context.model_dump(), ensure_ascii=False)) / 4)


def assert_safe(context: LlmSemanticContext) -> None:
    text = json.dumps(context.model_dump(), ensure_ascii=False)
    m = _LEAK.search(text)
    if m:
        raise ContextLeakError(f"context contains unsafe text: {m.group(0)!r}")


def build(*, request_id: str, catalog_version: str, metrics: list[SemanticDocument],
          dimensions: list[SemanticDocument], glossary: list[SemanticDocument],
          rules: list[SemanticDocument], max_tokens: int,
          domains: list[str]) -> tuple[LlmSemanticContext, int]:
    """Inputs are ranked best-first. Drops lowest-ranked entries until within ``max_tokens``."""
    metrics = list(metrics[:MAX_METRICS])
    glossary = list(glossary[:MAX_GLOSSARY])
    rules = list(rules[:MAX_RULES])

    def dims_for(ms: list[SemanticDocument]) -> list[SemanticDocument]:
        allowed = {n for m in ms for n in m.llm_safe_payload["allowed_dimensions"]}
        return [d for d in dimensions if d.canonical_name in allowed][:MAX_DIMENSIONS]

    def make(ms, ds, gl, rl) -> LlmSemanticContext:
        names = {d.canonical_name for d in ds}
        safe_metrics = []
        for m in ms:
            p = dict(m.llm_safe_payload)
            p["allowed_dimensions"] = [n for n in p["allowed_dimensions"] if n in names]
            safe_metrics.append(LlmSafeMetric(**p))
        used_domains = [d for d in domains if d][:3] or sorted({m.domain for m in ms})[:3]
        return LlmSemanticContext(
            request_id=request_id, catalog_version=catalog_version, selected_domains=used_domains,
            metrics=safe_metrics, dimensions=[LlmSafeDimension(**d.llm_safe_payload) for d in ds],
            glossary=[LlmSafeGlossaryEntry(**g.llm_safe_payload) for g in gl],
            ambiguity_rules=[LlmSafeAmbiguityRule(**r.llm_safe_payload) for r in rl],
            response_rules=list(DOMAIN_RULES),
        )

    dims = dims_for(metrics)
    context = make(metrics, dims, glossary, rules)
    while estimate_tokens(context) > max_tokens:
        if len(metrics) > 1:
            metrics.pop()
            dims = dims_for(metrics)
        elif glossary:
            glossary.pop()
        elif dims:
            dims.pop()
        elif rules:
            rules.pop()
        elif metrics:
            metrics.pop()
        else:
            break
        context = make(metrics, dims, glossary, rules)
    assert_safe(context)
    return context, estimate_tokens(context)
