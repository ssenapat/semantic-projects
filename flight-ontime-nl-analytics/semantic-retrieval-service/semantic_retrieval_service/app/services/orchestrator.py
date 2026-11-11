"""ask / retrieve orchestration (REQ-005/007/008/023/024/028/029)."""
from __future__ import annotations

import re
import time
import uuid

from ..observability.logging_config import log_event, question_fields
from ..observability.metrics import Metrics
from ..schemas.ask import AskRequest, AskResponse, RetrieveRequest, RetrieveResponse
from ..schemas.errors import ApiError
from ..schemas.parser import IntentParserRequest, ParserOptions
from ..schemas.result import QueryExecuteRequest
from .context_retriever import NoRetrievableContext
from .placeholder_authorizer import PlaceholderAuthorizer


# A2: /ask-only refusal of out-of-scope / injection questions when nothing is retrievable.
OUT_OF_SCOPE = re.compile(
    r"(?i)\b(ignore|disregard|forget|override)\b.{0,60}\b(instructions?|prompts?|rules?|system)\b|\bsql\b")


class _OutOfScope(Exception):
    pass


class Orchestrator:
    def __init__(self, retriever, parser_client, query_client, rate_limiter, metrics: Metrics,
                 authorizer: PlaceholderAuthorizer | None = None):
        self.retriever = retriever
        self.catalog_error: str | None = None
        self.parser = parser_client
        self.query = query_client
        self.limiter = rate_limiter
        self.metrics = metrics
        self.authorizer = authorizer or PlaceholderAuthorizer()

    # -- helpers ---------------------------------------------------------------------------
    def _versions(self) -> tuple[str, str]:
        if self.retriever is None:
            return "", ""
        st = self.retriever.status()
        return st.get("catalog_version", ""), st.get("semantic_version", "")

    def _admit(self, question: str, request_id: str) -> None:
        if not question.strip():
            raise ApiError("invalid_request", "The question must not be empty.", request_id)
        if not self.limiter.allow():
            raise ApiError("rate_limited", "Too many requests; please retry shortly.", request_id)
        if self.retriever is None:
            raise ApiError("catalog_not_ready", self.catalog_error or "The semantic catalog is not loaded.", request_id)

    def _retrieve(self, req, request_id: str, out_of_scope_refusal: bool = False):
        t0 = time.perf_counter()
        try:
            ctx, summary = self.retriever.retrieve(
                req.question, req.conversation_context, request_id,
                allowed_domains=self.authorizer.allowed_domains(req.authorization_context))
        except NoRetrievableContext:
            if out_of_scope_refusal and OUT_OF_SCOPE.search(req.question):
                raise _OutOfScope()
            raise ApiError("no_retrievable_context", "No approved metric matched the question.", request_id)
        self.metrics.retrieval((time.perf_counter() - t0) * 1000, summary.context_tokens_estimate,
                               summary.low_confidence)
        return ctx, summary

    def _run(self, event: str, request_id: str, question: str, fn):
        start = time.perf_counter()
        self.metrics.request()
        cv, sv = self._versions()
        outcome, code = "error", None
        try:
            result = fn()
            outcome = getattr(result, "outcome", "result")
            return result
        except ApiError as exc:
            code = exc.code
            raise
        finally:
            log_event(event, request_id=request_id, catalog_version=cv, semantic_version=sv, outcome=outcome,
                      latency_ms=int((time.perf_counter() - start) * 1000),
                      level="INFO" if code is None else "WARNING", error_code=code,
                      **question_fields(question))

    # -- public ----------------------------------------------------------------------------
    def retrieve(self, req: RetrieveRequest, request_id: str | None = None) -> RetrieveResponse:
        request_id = request_id or uuid.uuid4().hex

        def go():
            self._admit(req.question, request_id)
            ctx, summary = self._retrieve(req, request_id)
            return RetrieveResponse(request_id=request_id, catalog_version=ctx.catalog_version,
                                    authorization_mode=self.authorizer.mode, semantic_context=ctx,
                                    retrieval_summary=summary)
        return self._run("retrieve", request_id, req.question, go)

    def ask(self, req: AskRequest, request_id: str | None = None) -> AskResponse:
        request_id = request_id or uuid.uuid4().hex

        def go() -> AskResponse:
            self._admit(req.question, request_id)
            try:
                ctx, summary = self._retrieve(req, request_id, out_of_scope_refusal=True)
            except _OutOfScope:
                return AskResponse(
                    request_id=request_id, catalog_version=self._versions()[0],
                    authorization_mode=self.authorizer.mode, outcome="unsupported",
                    retrieval_summary={
                        "selected_domains": [], "metrics": [], "dimensions": [], "glossary_terms": [],
                        "ambiguity_triggers": [], "match_sources": {},
                        "retrieval_path": "keyword+vector" if self.retriever._vector_enabled else "keyword", "context_tokens_estimate": 0,
                        "low_confidence": True, "eligibility_audience": self.retriever._audience},
                    unsupported={"message": "I can only answer questions about the supported flight metrics.",
                                 "reason": "unsupported_operation", "suggested_supported_topics": []})
            cv = ctx.catalog_version

            def reply(outcome: str, **kw) -> AskResponse:
                return AskResponse(request_id=request_id, catalog_version=cv,
                                   authorization_mode=self.authorizer.mode, outcome=outcome,
                                   retrieval_summary=summary.model_dump(mode="json"), **kw)

            parsed = self.parser.parse(IntentParserRequest(
                request_id=request_id, question=req.question, conversation_context=req.conversation_context,
                semantic_context=ctx, parser_options=ParserOptions()))
            if parsed.catalog_version != cv:
                raise ApiError("catalog_version_mismatch", "The parser used a different catalog version.", request_id)
            if parsed.outcome == "clarification_required":
                return reply("clarification_required", clarification=parsed.clarification.model_dump(mode="json"))
            if parsed.outcome == "unsupported":
                return reply("unsupported", unsupported=parsed.unsupported.model_dump(mode="json"))

            sem = parsed.semantic_request.model_copy(
                update={"requested_limit": min(req.requested_limit, parsed.semantic_request.requested_limit)})
            qres = self.query.execute(QueryExecuteRequest(
                request_id=request_id, catalog_version=cv, semantic_request=sem,
                authorization_context=req.authorization_context))
            if qres.outcome == "clarification_required":
                return reply("clarification_required", clarification=qres.clarification.model_dump(mode="json"))
            if qres.outcome == "unsupported":
                return reply("unsupported", unsupported=qres.unsupported.model_dump(mode="json"))
            if qres.metadata.catalog_version != cv:
                raise ApiError("catalog_version_mismatch", "The query service used a different catalog version.",
                               request_id)
            return reply("result", result=qres.model_dump(mode="json"))
        return self._run("ask", request_id, req.question, go)
