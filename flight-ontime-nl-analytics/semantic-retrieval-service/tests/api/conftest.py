import pytest
from fastapi.testclient import TestClient

from semantic_retrieval_service.app.config import Settings
from semantic_retrieval_service.app.main import create_app
from semantic_retrieval_service.app.schemas.parser import (
    ClarificationOutcome, SemanticRequestOutcome, UnsupportedOutcome)
from semantic_retrieval_service.app.schemas.result import (
    QueryClarificationResponse, QueryResultResponse, QueryUnsupportedResponse)
from semantic_retrieval_service.app.services import catalog_loader
from semantic_retrieval_service.app.services.context_retriever import ContextRetriever
from pathlib import Path

DEFS = str(Path(__file__).resolve().parents[3] / "semantic-layer" / "definitions")
QUESTION = "average arrival delay by carrier"


@pytest.fixture(scope="session")
def retriever():
    return ContextRetriever(catalog_loader.load(DEFS, "internal"))


class StubParser:
    def __init__(self, kind="semantic_request"):
        self.kind, self.calls, self.catalog_version = kind, [], None

    def parse(self, req):
        self.calls.append(req)
        cv = self.catalog_version or req.semantic_context.catalog_version
        if self.kind == "clarification_required":
            return ClarificationOutcome(outcome=self.kind, catalog_version=cv, confidence=0.5,
                                        clarification={"question": "Which airport?", "ambiguity_type": "scope"})
        if self.kind == "unsupported":
            return UnsupportedOutcome(outcome=self.kind, catalog_version=cv, confidence=0.5,
                                      unsupported={"message": "No.", "reason": "no_approved_metric"})
        if isinstance(self.kind, Exception):
            raise self.kind
        metric = req.semantic_context.metrics[0].name
        return SemanticRequestOutcome(outcome="semantic_request", catalog_version=cv, confidence=0.9,
                                      semantic_request={"metrics": [metric], "requested_limit": 1000})


class StubQuery:
    def __init__(self, kind="result"):
        self.kind, self.calls = kind, []

    def execute(self, req):
        self.calls.append(req)
        if isinstance(self.kind, Exception):
            raise self.kind
        if self.kind == "clarification_required":
            return QueryClarificationResponse(outcome=self.kind, request_id=req.request_id,
                                              clarification={"question": "Which Chicago airport?",
                                                             "ambiguity_type": "airport_or_location"})
        if self.kind == "unsupported":
            return QueryUnsupportedResponse(outcome=self.kind, request_id=req.request_id,
                                            unsupported={"message": "No.", "reason": "unsupported_operation"})
        return QueryResultResponse(
            outcome="result", request_id=req.request_id,
            columns=[{"name": "x", "role": "metric", "label": "X", "unit": "count", "semantic_type": "metric",
                      "data_type": "integer"}],
            rows=[[1.0]], truncated=False, row_count=1,
            metric_versions={m: 1 for m in req.semantic_request.metrics},
            metadata={"metrics": req.semantic_request.metrics, "catalog_version": req.catalog_version,
                      "time_basis": None, "applied_filters": [], "generated_at_utc": "2026-01-01T00:00:00Z"},
            chart_hints=[{"chart_type": "table", "confidence": 1.0}],
            query_execution={"elapsed_ms": 1, "effective_limit": req.semantic_request.requested_limit,
                             "reference_date": "2026-01-01", "resolved_date_range": None,
                             "explain_checked": False, "sql_guard": "passed"})


@pytest.fixture
def make_client(retriever):
    def make(parser=None, query=None, ready=True, **settings):
        app = create_app(Settings(**settings), retriever=retriever if ready else None,
                         parser_client=parser or StubParser(), query_client=query or StubQuery(),
                         load_catalog=False)
        return TestClient(app, raise_server_exceptions=False)
    return make
