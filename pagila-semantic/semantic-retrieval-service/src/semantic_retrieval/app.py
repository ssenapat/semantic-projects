"""HTTP API: POST /v1/retrieve returns the catalog entries relevant to a question."""
from __future__ import annotations

import os

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .index import CatalogIndex
from .query_client import CatalogSource, CatalogUnavailable


class RetrieveRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    limit: int | None = Field(default=None, ge=1, le=50)


def create_app(source: CatalogSource | None = None) -> FastAPI:
    app = FastAPI(title="semantic-retrieval-service")
    src = source or CatalogSource(os.environ.get("SEMANTIC_QUERY_URL", "http://127.0.0.1:8000"),
                                  os.environ.get("SEMANTIC_QUERY_API_KEY"))
    keys = {k for k in os.environ.get("RETRIEVAL_API_KEYS", "").split(",") if k}
    state: dict = {"catalog": None, "index": None}

    def index() -> CatalogIndex:
        catalog = src.get()
        if state["catalog"] is not catalog:
            state["catalog"], state["index"] = catalog, CatalogIndex(catalog)
        return state["index"]

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/ready")
    def ready():
        try:
            index()
        except CatalogUnavailable:
            raise HTTPException(503, "catalog_unavailable")
        return {"status": "ready"}

    @app.post("/v1/retrieve")
    def retrieve(body: RetrieveRequest, x_api_key: str | None = Header(default=None)):
        if keys and x_api_key not in keys:
            raise HTTPException(401, "unauthorized")
        try:
            idx = index()
            limit = body.limit
            if limit is None:
                policy = src.policy()
                limit = policy["max_measures_per_query"] + policy["max_dimensions_per_query"]
            hits = idx.search(body.question, limit)
        except CatalogUnavailable:
            raise HTTPException(503, "catalog_unavailable")
        if not hits:
            raise HTTPException(422, "no_retrievable_context")
        ambiguous = idx.ambiguous(body.question)
        hits = list(hits)
        ranked = {(e.model, e.name) for e, _ in hits}
        for term in ambiguous:
            for m in term["matches"]:
                if (m["model"], m["name"]) not in ranked:
                    hits.append((idx.entry(m["model"], m["name"]), 0.0))
                    ranked.add((m["model"], m["name"]))
        models = {e.model for e, _ in hits}
        return {
            "models": sorted(models),
            "matches": [{"kind": e.kind, "model": e.model, "name": e.name, "label": e.label,
                         "description": e.description, "score": round(s, 4), **e.extra}
                        for e, s in hits],
            "ambiguous_terms": ambiguous,
            "catalog": {m: state["catalog"][m] for m in sorted(models)},
        }

    return app


app = create_app
