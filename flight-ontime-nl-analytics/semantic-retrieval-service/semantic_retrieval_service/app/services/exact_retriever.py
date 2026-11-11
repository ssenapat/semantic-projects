"""Exact match on canonical_name / label (REQ-016)."""
from __future__ import annotations

from ..schemas.catalog import SemanticDocument
from .document_normalizer import humanize, normalize_text


def contains_phrase(text: str, phrase: str) -> bool:
    """Whole-word phrase containment over already-normalised text."""
    return bool(phrase) and f" {phrase} " in f" {text} "


def search(query: str, docs: list[SemanticDocument]) -> list[tuple[str, float]]:
    q = normalize_text(query)
    hits: list[tuple[str, float]] = []
    for d in docs:
        for phrase in {normalize_text(d.canonical_name), normalize_text(humanize(d.canonical_name)), normalize_text(d.label)}:
            if contains_phrase(q, phrase):
                hits.append((d.document_id, float(len(phrase))))
                break
    return sorted(hits, key=lambda h: (-h[1], h[0]))
