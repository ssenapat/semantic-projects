"""Optional vector retrieval (REQ-019, HC-1). A no-op unless RETRIEVER_VECTOR_ENABLED is true.

The only provider implemented here is the offline ``hash`` provider (hashed character trigrams,
cosine similarity). ``RETRIEVER_EMBEDDING_API_KEY`` is read only when a remote provider is added.
"""
from __future__ import annotations

import hashlib
import math

from ..schemas.catalog import SemanticDocument
from .document_normalizer import normalize_text

DIM = 256
TOP_K = 20


def embed(text: str) -> list[float]:
    t = f"  {normalize_text(text)}  "
    vec = [0.0] * DIM
    for i in range(len(t) - 2):
        h = int(hashlib.md5(t[i : i + 3].encode()).hexdigest(), 16)
        vec[h % DIM] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


class VectorRetriever:
    def __init__(self, docs: list[SemanticDocument], enabled: bool = False, provider: str = "hash"):
        self.enabled = enabled
        self._ids: list[str] = []
        self._vecs: list[list[float]] = []
        if enabled:
            if provider not in ("hash", "local"):
                raise ValueError("RETRIEVER_VECTOR_ENABLED requires RETRIEVER_EMBEDDING_PROVIDER=hash")
            self._ids = [d.document_id for d in docs]
            self._vecs = [embed(d.searchable_text) for d in docs]

    def search(self, query: str, top_k: int = TOP_K) -> list[tuple[str, float]]:
        if not self.enabled or not self._vecs:
            return []
        q = embed(query)
        scored = [(i, sum(a * b for a, b in zip(q, v))) for i, v in zip(self._ids, self._vecs)]
        scored = [s for s in scored if s[1] > 0.2]
        return sorted(scored, key=lambda h: (-h[1], h[0]))[:top_k]

    def vectors(self) -> tuple[list[str], list[list[float]]]:
        return self._ids, self._vecs
