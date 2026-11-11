"""Reciprocal rank fusion plus boosts (REQ-020)."""
from __future__ import annotations

EXACT_BOOST = 0.50
SYNONYM_BOOST = 0.30
DOMAIN_BOOST = 0.05


def rrf(lists: list[list[str]], k: int = 60) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranked in lists:
        for rank, doc_id in enumerate(ranked, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def fuse(*, exact: list[str], synonym: list[str], bm25: list[str], vector: list[str] | None = None,
         domains: dict[str, str] | None = None, preferred_domains: set[str] | None = None,
         k: int = 60) -> list[tuple[str, float]]:
    """Return ``[(doc_id, score)]`` ranked by score desc, ties by id (canonical_name) asc."""
    lists = [l for l in (exact, synonym, bm25, vector or []) if l]
    scores = rrf(lists, k)
    for d in exact:
        scores[d] = scores.get(d, 0.0) + EXACT_BOOST
    for d in synonym:
        scores[d] = scores.get(d, 0.0) + SYNONYM_BOOST
    if domains and preferred_domains:
        for d in scores:
            if domains.get(d) in preferred_domains:
                scores[d] += DOMAIN_BOOST
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0].split(":", 1)[-1], kv[0]))
