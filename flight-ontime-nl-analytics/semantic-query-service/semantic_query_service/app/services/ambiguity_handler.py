"""Builds clarification / unsupported outcomes (REQ-051, REQ-052)."""
from __future__ import annotations

from ..schemas.semantic_request import (ClarificationDetails, QueryClarificationResponse,
                                        QueryUnsupportedResponse, UnsupportedDetails)


class NeedsClarification(Exception):
    def __init__(self, question: str, ambiguity_type: str, options: list[str] | None = None):
        super().__init__(question)
        self.question = question
        self.ambiguity_type = ambiguity_type
        self.options = list(options or [])


class Unresolvable(Exception):
    def __init__(self, message: str, reason: str = "insufficient_context",
                 conflicting_metrics: list[str] | None = None):
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.conflicting_metrics = list(conflicting_metrics or [])


def clarification(request_id: str, question: str, ambiguity_type: str,
                  options: list[str] | None = None) -> QueryClarificationResponse:
    return QueryClarificationResponse(
        outcome="clarification_required", request_id=request_id,
        clarification=ClarificationDetails(question=question[:500], ambiguity_type=ambiguity_type,
                                           candidate_options=list(options or [])[:10]))


def unsupported(request_id: str, reason: str, message: str, topics: list[str] | None = None,
                conflicting_metrics: list[str] | None = None) -> QueryUnsupportedResponse:
    return QueryUnsupportedResponse(
        outcome="unsupported", request_id=request_id,
        unsupported=UnsupportedDetails(message=message[:500] or "Unsupported request", reason=reason,
                                       suggested_supported_topics=list(topics or [])[:10],
                                       conflicting_metrics=list(conflicting_metrics or [])))


def from_exception(request_id: str, exc: Exception):
    if isinstance(exc, NeedsClarification):
        return clarification(request_id, exc.question, exc.ambiguity_type, exc.options)
    if isinstance(exc, Unresolvable):
        return unsupported(request_id, exc.reason, exc.message, conflicting_metrics=exc.conflicting_metrics)
    raise exc
