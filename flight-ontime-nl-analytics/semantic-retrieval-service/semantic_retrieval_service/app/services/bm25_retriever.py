"""BM25 (Okapi) over searchable_text, top 20 (REQ-018). Uses rank_bm25 when installed, else a built-in copy."""
from __future__ import annotations

import math

from ..schemas.catalog import SemanticDocument
from .document_normalizer import normalize_text

TOP_K = 20
STOPWORDS = frozenset(
    "a an the of for by in on at to from and or with show me what how many much is are was were do does "
    "give list get tell per each all any last this that it its as be total number".split()
)


def _stem(token: str) -> str:
    return token[:-1] if len(token) > 3 and token.endswith("s") and not token.endswith("ss") else token


def tokenize(text: str) -> list[str]:
    return [_stem(t) for t in normalize_text(text).split() if t not in STOPWORDS]


class _Okapi:
    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75, epsilon: float = 0.25):
        self.k1, self.b = k1, b
        self.n = len(corpus)
        self.lens = [len(d) for d in corpus]
        self.avg = (sum(self.lens) / self.n) if self.n else 0.0
        self.freqs = []
        df: dict[str, int] = {}
        for doc in corpus:
            f: dict[str, int] = {}
            for w in doc:
                f[w] = f.get(w, 0) + 1
            self.freqs.append(f)
            for w in f:
                df[w] = df.get(w, 0) + 1
        self.idf: dict[str, float] = {}
        neg = []
        total = 0.0
        for w, n in df.items():
            v = math.log(self.n - n + 0.5) - math.log(n + 0.5)
            self.idf[w] = v
            total += v
            if v < 0:
                neg.append(w)
        floor = epsilon * (total / len(self.idf)) if self.idf else 0.0
        for w in neg:
            self.idf[w] = floor

    def get_scores(self, query: list[str]) -> list[float]:
        scores = [0.0] * self.n
        for w in query:
            idf = self.idf.get(w)
            if idf is None:
                continue
            for i, f in enumerate(self.freqs):
                tf = f.get(w, 0)
                if tf:
                    scores[i] += idf * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * self.lens[i] / (self.avg or 1)))
        return scores


def _make(corpus):
    try:
        from rank_bm25 import BM25Okapi

        return BM25Okapi(corpus)
    except ImportError:
        return _Okapi(corpus)


class Bm25Index:
    def __init__(self, docs: list[SemanticDocument]):
        self._ids = [d.document_id for d in docs]
        self._bm25 = _make([tokenize(d.searchable_text) or ["_"] for d in docs]) if docs else None

    def search(self, query: str, top_k: int = TOP_K) -> list[tuple[str, float]]:
        tokens = tokenize(query)
        if not self._bm25 or not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked = sorted(((self._ids[i], float(s)) for i, s in enumerate(scores) if s > 0), key=lambda h: (-h[1], h[0]))
        return ranked[:top_k]


def search(query: str, docs: list[SemanticDocument], top_k: int = TOP_K) -> list[tuple[str, float]]:
    return Bm25Index(docs).search(query, top_k)
