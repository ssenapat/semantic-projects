import json

from semantic_retrieval_service.app.schemas.retrieval import LlmSemanticContext
from semantic_retrieval_service.app.services.context_retriever import ContextRetriever
from semantic_retrieval_service.app.services.safe_context_builder import assert_safe, estimate_tokens


def test_context_is_safe_and_bounded(retriever):
    ctx, summary = retriever.retrieve("flight count cancellation rate by carrier and origin city", [], "rid")
    assert isinstance(ctx, LlmSemanticContext)
    assert len(ctx.metrics) <= 8 and len(ctx.dimensions) <= 50 and len(ctx.glossary) <= 15 and len(ctx.ambiguity_rules) <= 20
    assert 1 <= len(ctx.selected_domains) <= 3
    text = json.dumps(ctx.model_dump()).lower()
    for bad in ("ontime", "select ", "join ", "fl_date", "tran-stats"):
        assert bad not in text
    assert summary.context_tokens_estimate == estimate_tokens(ctx)
    assert_safe(ctx)


def test_token_budget_drops_metrics(loaded):
    small = ContextRetriever(loaded, max_context_tokens=1500)
    ctx, summary = small.retrieve("flight count cancellation rate diversions late arrivals", [], "rid")
    assert summary.context_tokens_estimate <= 1500
    big, _ = ContextRetriever(loaded).retrieve("flight count cancellation rate diversions late arrivals", [], "rid")
    assert len(ctx.metrics) < len(big.metrics)


def test_metric_catalog_and_status(retriever):
    cat = retriever.metric_catalog()
    names = [m.name for m in cat.metrics]
    assert names == sorted(names) and "diversion_rate" not in names
    text = cat.model_dump_json().lower()
    assert "ontime" not in text and "count(*)" not in text and "fl_date" not in text
    assert next(m for m in cat.metrics if m.name == "flight_count").time_basis == "flight date"
    st = retriever.status()
    assert st["ready"] and st["retrieval_mode"] == "keyword" and st["vector_enabled"] is False
    assert st["counts"]["metrics"] == len(names)
    assert st["counts_by_status"]["draft"] >= 1
    assert "/" not in json.dumps(st).replace("T", "")  # no paths
