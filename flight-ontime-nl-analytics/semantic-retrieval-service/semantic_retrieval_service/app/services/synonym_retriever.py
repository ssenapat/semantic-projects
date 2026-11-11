"""Synonym match (REQ-017)."""
from __future__ import annotations

from ..schemas.catalog import SemanticDocument
from .document_normalizer import normalize_text
from .exact_retriever import contains_phrase


def search(query: str, docs: list[SemanticDocument]) -> list[tuple[str, float]]:
    q = normalize_text(query)
    hits: list[tuple[str, float]] = []
    for d in docs:
        best = 0
        for syn in d.synonyms:
            phrase = normalize_text(syn)
            if contains_phrase(q, phrase):
                best = max(best, len(phrase))
        if best:
            hits.append((d.document_id, float(best)))
    return sorted(hits, key=lambda h: (-h[1], h[0]))
