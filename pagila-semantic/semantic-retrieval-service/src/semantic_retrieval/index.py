"""Builds a searchable index from the query service catalog and ranks it with BM25."""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path
from dataclasses import dataclass, field

# Business words people use, mapped to the words used in the catalog.
def load_glossary(path: str | None = None) -> dict[str, list[str]]:
    """Synonyms come from glossary.json, or from the file named by RETRIEVAL_GLOSSARY."""
    path = path or os.environ.get("RETRIEVAL_GLOSSARY") or str(Path(__file__).with_name("glossary.json"))
    return json.loads(Path(path).read_text())["synonyms"]


def load_ambiguous(path: str | None = None) -> dict[str, list[str]]:
    """Ambiguous words (word -> candidate dimension names) from the same glossary file."""
    path = path or os.environ.get("RETRIEVAL_GLOSSARY") or str(Path(__file__).with_name("glossary.json"))
    return json.loads(Path(path).read_text()).get("ambiguous", {})


SYNONYMS = load_glossary()
AMBIGUOUS = load_ambiguous()

_TOKEN = re.compile(r"[a-z0-9]+")

STOPWORDS = frozenset("""a an the is are was were be of in on at to for by with and or not
what which who whom how many much do does did show me give list per each all from that this
these those it its as than then there their i we you my our your""".split())


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower().replace("_", " ")):
        if t in STOPWORDS:
            continue
        if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


def expand(question: str) -> list[str]:
    base = tokens(question)
    extra: list[str] = []
    for raw in _TOKEN.findall(question.lower()):
        for syn in SYNONYMS.get(raw, []):
            extra.extend(tokens(syn))
    return base + extra


@dataclass
class Entry:
    kind: str            # "measure" or "dimension"
    model: str
    name: str
    label: str
    description: str
    extra: dict = field(default_factory=dict)
    terms: list[str] = field(default_factory=list)

    def text(self) -> str:
        return f"{self.name} {self.label} {self.description}"


class CatalogIndex:
    def __init__(self, catalog: dict, k1: float = 1.5, b: float = 0.75):
        self.catalog, self.k1, self.b = catalog, k1, b
        self.entries: list[Entry] = []
        for model, m in catalog.items():
            for x in m.get("measures", []):
                self.entries.append(Entry("measure", model, x["name"], x.get("label", ""),
                                          x.get("description", ""),
                                          {"unit": x.get("unit"),
                                           "combinable_with": x.get("combinable_with"),
                                           "allowed_dimensions": x.get("allowed_dimensions")}))
            for d in m.get("dimensions", []):
                self.entries.append(Entry("dimension", model, d["name"], d.get("label", ""),
                                          d.get("description", ""),
                                          {"type": d.get("type"),
                                           "granularities": d.get("granularities")}))
        for e in self.entries:
            # Names are repeated so an exact name hit outweighs a passing mention.
            e.terms = tokens(e.text()) + tokens(e.name) * 2
        n = len(self.entries) or 1
        self.avg_len = sum(len(e.terms) for e in self.entries) / n
        df: dict[str, int] = {}
        for e in self.entries:
            for t in set(e.terms):
                df[t] = df.get(t, 0) + 1
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def score(self, entry: Entry, query: list[str]) -> float:
        total = 0.0
        length = len(entry.terms) or 1
        for t in set(query):
            f = entry.terms.count(t)
            if not f:
                continue
            norm = f + self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
            total += self.idf.get(t, 0) * f * (self.k1 + 1) / norm
        return total

    def search(self, question: str, limit: int = 10) -> list[tuple[Entry, float]]:
        q = expand(question)
        qset = set(q)
        scored = [(e, self.score(e, q)) for e in self.entries]
        scored = [(e, s) for e, s in scored if s > 0]
        scored.sort(key=lambda p: (-p[1], p[0].name))
        # Entries whose whole name is mentioned in the question are always candidates.
        mentioned = [p for p in scored if set(tokens(p[0].name)) <= qset]
        rest = [p for p in scored if not set(tokens(p[0].name)) <= qset]
        picked = (mentioned + rest)[:limit]
        picked.sort(key=lambda p: (-p[1], p[0].name))
        return picked

    def ambiguous(self, question: str) -> list[dict]:
        """Ambiguous words in the question with the catalog dimensions they may mean."""
        out, seen = [], set()
        for word in _TOKEN.findall(question.lower()):
            if word in seen or word not in AMBIGUOUS:
                continue
            seen.add(word)
            wanted = set(AMBIGUOUS[word])
            found = sorted((e.model, e.name) for e in self.entries
                           if e.kind == "dimension" and e.name in wanted)
            if found:
                out.append({"term": word, "matches": [{"model": m, "name": n} for m, n in found]})
        return out

    def entry(self, model: str, name: str) -> Entry | None:
        for e in self.entries:
            if e.model == model and e.name == name and e.kind == "dimension":
                return e
        return None
