"""C-2.1 / C-2.2 / C-2.3 / C-3.3 / C-2.7 for the retriever copies (REQ-001..004, 006, 010, 021, 022, 033)."""
import pytest
from pydantic import ValidationError

from semantic_retrieval_service.app.policy_limits import MAX_METRICS, load_max_metrics_per_query
from semantic_retrieval_service.app.schemas.parser import ConstrainedSemanticRequest, UnsupportedDetails
from semantic_retrieval_service.app.schemas.retrieval import LlmSafeMetric


def req(**kw):
    return ConstrainedSemanticRequest(**kw)


def test_metrics_bounds_and_uniqueness():
    assert MAX_METRICS == 5
    assert req(metrics=["a"]).metrics == ["a"]
    assert len(req(metrics=list("abcde")).metrics) == 5
    for bad in ([], list("abcdef"), ["a", "a"]):
        with pytest.raises(ValidationError):
            req(metrics=bad)


def test_legacy_metric_and_missing_metrics_rejected():
    with pytest.raises(ValidationError) as e:
        req(metric="a")
    types = {x["type"] for x in e.value.errors()}
    assert {"missing", "extra_forbidden"} <= types
    with pytest.raises(ValidationError):
        req(metrics=["a"], metric="a")


def test_group_by_limits_and_unique_pairs():
    gb = lambda n: [{"dimension": f"d{i}"} for i in range(n)]  # noqa: E731
    for n in (0, 1, 8):
        assert len(req(metrics=["a"], group_by=gb(n)).group_by) == n
    with pytest.raises(ValidationError):
        req(metrics=["a"], group_by=gb(9))
    with pytest.raises(ValidationError):
        req(metrics=["a"], group_by=[{"dimension": "d"}, {"dimension": "d"}])
    with pytest.raises(ValidationError):
        req(metrics=["a"], group_by=[{"dimension": "d", "time_grain": "month"}] * 2)
    ok = req(metrics=["a"], group_by=[{"dimension": "d", "time_grain": "month"}, {"dimension": "d"}])
    assert len(ok.group_by) == 2


def test_unsupported_details_conflicting_metrics():
    u = UnsupportedDetails(message="m", reason="metrics_not_combinable", conflicting_metrics=["a", "b"])
    assert u.conflicting_metrics == ["a", "b"]
    assert UnsupportedDetails(message="m", reason="no_approved_metric").conflicting_metrics == []
    with pytest.raises(ValidationError):
        UnsupportedDetails(message="m", reason="no_approved_metric", conflicting_metrics=list("abcdef"))


def test_llm_safe_metric_combinable_with():
    base = dict(name="n", label="l", description="d", domain="x", allowed_dimensions=[])
    assert LlmSafeMetric(**base).combinable_with is None
    assert LlmSafeMetric(**base, combinable_with="g").combinable_with == "g"


def test_policy_limits_errors(tmp_path):
    (tmp_path / "policies").mkdir()
    with pytest.raises(RuntimeError, match="query_policy.yml"):
        load_max_metrics_per_query(tmp_path)
    f = tmp_path / "policies" / "query_policy.yml"
    for body in ("other: 1\n", "max_metrics_per_query: 0\n", "max_metrics_per_query: x\n"):
        f.write_text(body)
        with pytest.raises(RuntimeError, match="max_metrics_per_query"):
            load_max_metrics_per_query(tmp_path)
    f.write_text("max_metrics_per_query: 7\n")
    assert load_max_metrics_per_query(tmp_path) == 7


def test_context_combinable_with_and_coverage(retriever):
    q = "Flights, cancellations and cancellation rate last month by carrier"
    ctx, _ = retriever.retrieve(q, [], "r1")
    names = {m.name for m in ctx.metrics}
    assert {"flight_count", "cancelled_flight_count", "cancellation_rate"} <= names
    assert all(m.combinable_with == "flight_operations" for m in ctx.metrics)
    assert "carrier" in {g.term.lower() for g in ctx.glossary}


def test_context_exposes_only_catalog_items(retriever, loaded):
    q = "Flights and cancelled flights by carrier and origin airport"
    ctx, _ = retriever.retrieve(q, [], "r2")
    cat = {d.canonical_name for d in loaded.documents if d.document_type == "metric"}
    assert {m.name for m in ctx.metrics} <= cat
    assert {"flight_count", "cancelled_flight_count"} <= {m.name for m in ctx.metrics}


def test_catalog_endpoint_model_has_combinable_with(retriever):
    cat = retriever.metric_catalog()
    assert cat.metrics and all(m.combinable_with == "flight_operations" for m in cat.metrics)
