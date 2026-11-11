"""Hybrid retrieval over eligible documents -> LlmSemanticContext (CONTRACT §7.1, REQ-016..022)."""
from __future__ import annotations

from collections import Counter

from ..schemas.catalog import SemanticDocument
from ..schemas.retrieval import (LlmSemanticContext, MetricCatalogResponse, MetricDefinition, MetricDimensionRef,
                                 RetrievalSummary)
from . import exact_retriever, rank_fusion, safe_context_builder, synonym_retriever
from .bm25_retriever import Bm25Index
from .catalog_loader import LoadedCatalog
from .vector_retriever import VectorRetriever

LOW_CONFIDENCE_SCORE = 0.02


class NoRetrievableContext(Exception):
    """No document matched the question."""


class ContextRetriever:
    def __init__(self, loaded: LoadedCatalog, *, max_context_tokens: int = 12000, vector_enabled: bool = False,
                 audience: str = "internal", embedding_provider: str = "hash"):
        self._loaded = loaded
        self._docs = list(loaded.documents)
        self._by_id = {d.document_id: d for d in self._docs}
        self._max_tokens = max_context_tokens
        self._vector_enabled = vector_enabled
        self._audience = audience
        self._bm25 = Bm25Index(self._docs)
        self._vector = VectorRetriever(self._docs, enabled=vector_enabled, provider=embedding_provider)
        self._domains = {d.document_id: d.domain for d in self._docs}

    # ------------------------------------------------------------------ retrieval
    def rank(self, question: str, conversation_context: list[str] | None = None,
             allowed_domains: list[str] | None = None) -> tuple[list[tuple[str, float]], dict[str, list[str]]]:
        docs = [d for d in self._docs if not allowed_domains or d.domain in allowed_domains]
        allowed_ids = {d.document_id for d in docs}
        ctx = " ".join(conversation_context or [])

        def run(text: str):
            ex = [i for i, _ in exact_retriever.search(text, docs)]
            sy = [i for i, _ in synonym_retriever.search(text, docs) if i not in ex]
            bm = [i for i, _ in self._bm25.search(text) if i in allowed_ids]
            ve = [i for i, _ in self._vector.search(text) if i in allowed_ids]
            return ex, sy, bm, ve

        ex, sy, bm, ve = run(question)
        if not (ex or sy or bm or ve) and ctx:
            ex, sy, bm, ve = run(f"{ctx} {question}")
        fused = rank_fusion.fuse(exact=ex, synonym=sy, bm25=bm, vector=ve, domains=self._domains,
                                 preferred_domains=set(allowed_domains) if allowed_domains else None)
        sources: dict[str, list[str]] = {}
        for label, ids in (("exact", ex), ("synonym", sy), ("bm25", bm), ("vector", ve)):
            for i in ids:
                sources.setdefault(i, []).append(label)
        # exact beats synonym; a doc matched exactly is also a synonym candidate (same phrase list) -> keep both labels
        return fused, sources

    def retrieve(self, question: str, conversation_context: list[str], request_id: str,
                 allowed_domains: list[str] | None = None) -> tuple[LlmSemanticContext, RetrievalSummary]:
        fused, sources = self.rank(question, conversation_context, allowed_domains)
        if not fused:
            raise NoRetrievableContext("no catalog entry matches the question")
        by = lambda t: [self._by_id[i] for i, _ in fused if self._by_id[i].document_type == t]  # noqa: E731
        metrics = by("metric")
        glossary = by("glossary")
        matched_rules = by("ambiguity_rule") + by("unavailable_concept")

        # C-2.7 coverage invariant: exact/synonym matches outrank bm25/vector-only ones before any size cut.
        def strong_first(docs: list[SemanticDocument]) -> list[SemanticDocument]:
            return sorted(docs, key=lambda d: 0 if {"exact", "synonym"} & set(sources.get(d.document_id, ())) else 1)

        metrics = strong_first(metrics)
        glossary = strong_first(glossary)

        # Glossary hits may point at metrics/dimensions that text matching missed.
        picked = {m.document_id for m in metrics}
        for g in glossary:
            for target in g.llm_safe_payload.get("maps_to", []):
                doc = self._by_id.get(f"metric:{target}")
                if doc and doc.document_id not in picked:
                    metrics.append(doc)
                    picked.add(doc.document_id)
        all_rules = [d for d in self._docs if d.document_type in ("ambiguity_rule", "unavailable_concept")]
        promoted = [self._by_id[f"{t}:{g.internal_payload['rule']}"] for g in glossary
                    if "rule" in g.internal_payload
                    for t in ("ambiguity_rule", "unavailable_concept") if f"{t}:{g.internal_payload['rule']}" in self._by_id]
        matched_rules = list({r.document_id: r for r in promoted + matched_rules}.values())
        taken = {r.document_id for r in matched_rules}
        rules = matched_rules + [r for r in all_rules if r.document_id not in taken]

        ranked_dims = by("dimension")
        all_dims = [d for d in self._docs if d.document_type == "dimension"]
        dims = ranked_dims + [d for d in all_dims if d not in ranked_dims]
        domains = list(dict.fromkeys(d.domain for d in (metrics[:8] or ranked_dims or glossary) if d.domain))[:3]
        if not domains:  # A3: selected_domains would be empty (1..3 invariant) -> no eligible context
            raise NoRetrievableContext("no eligible metric remains after eligibility filtering")

        context, tokens = safe_context_builder.build(
            request_id=request_id, catalog_version=self._loaded.versions.catalog_version, metrics=metrics,
            dimensions=dims, glossary=glossary, rules=rules, max_tokens=self._max_tokens, domains=domains)

        metric_names = [m.name for m in context.metrics]
        dim_names = [d.name for d in context.dimensions]
        gloss_terms = [g.term for g in context.glossary]
        triggers = [r.trigger for r in context.ambiguity_rules]
        match_sources: dict[str, list[str]] = {}
        for doc_id, labels in sources.items():
            doc = self._by_id[doc_id]
            if doc.document_type not in ("metric", "dimension") or doc.canonical_name in metric_names + dim_names:
                match_sources[doc.canonical_name] = [l for l in ("exact", "synonym", "bm25", "vector") if l in labels]
        top = fused[0][1]
        low = top < LOW_CONFIDENCE_SCORE or not any(("exact" in v or "synonym" in v) for v in match_sources.values())
        summary = RetrievalSummary(
            selected_domains=context.selected_domains, metrics=metric_names, dimensions=dim_names,
            glossary_terms=gloss_terms, ambiguity_triggers=triggers, match_sources=match_sources,
            retrieval_path="keyword+vector" if self._vector_enabled else "keyword",
            context_tokens_estimate=tokens, low_confidence=low, eligibility_audience=self._audience)
        return context, summary

    # ------------------------------------------------------------------ catalog views
    def metric_catalog(self) -> MetricCatalogResponse:
        dims = {d.canonical_name: d.label for d in self._docs if d.document_type == "dimension"}
        items = []
        for d in sorted((d for d in self._docs if d.document_type == "metric"), key=lambda d: d.canonical_name):
            p, i = d.llm_safe_payload, d.internal_payload
            items.append(MetricDefinition(
                name=d.canonical_name, label=d.label, description=d.description, unit=i.get("unit"), status=d.status,
                time_basis=i.get("time_basis"),
                allowed_dimensions=[MetricDimensionRef(name=n, label=dims[n]) for n in p["allowed_dimensions"] if n in dims],
                allowed_time_grains=p["allowed_time_grains"], synonyms=p["synonyms"],
                clarification_notes=p["clarification_notes"], caveats=i.get("caveats", []), combinable_with=p.get("combinable_with")))
        return MetricCatalogResponse(catalog_version=self._loaded.versions.catalog_version, metrics=items)

    def status(self) -> dict:
        v = self._loaded.versions
        types = Counter(d.document_type for d in self._docs)
        cat = self._loaded.catalog
        by_status = {"approved": 0, "validated": 0, "draft": 0, "deprecated": 0}
        entries = [m.status for m in cat.metrics.values()] + [d.status for mo in cat.models.values() for d in mo.dimensions]
        for s in entries:
            if s in by_status:
                by_status[s] += 1
        return {
            "catalog_version": v.catalog_version, "semantic_version": v.semantic_version,
            "source_checksum": v.source_checksum, "build_timestamp": v.build_timestamp, "ready": True,
            "retrieval_mode": "keyword+vector" if self._vector_enabled else "keyword",
            "vector_enabled": self._vector_enabled, "eligibility_audience": self._audience,
            "counts": {"documents": len(self._docs), "metrics": types["metric"], "dimensions": types["dimension"],
                       "glossary": types["glossary"],
                       "ambiguity_rules": types["ambiguity_rule"] + types["unavailable_concept"]},
            "counts_by_status": by_status,
        }
