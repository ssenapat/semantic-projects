import threading
import time
from collections import deque

from fastapi import APIRouter, Request

from ..config import not_ready_detail
from ..errors import ParserError
from ..schemas.request import IntentParserRequest
from ..schemas.response import IntentParserResponse
from ..services.fallback_handler import parse_intent
from ..services.response_validator import has_context_code

router = APIRouter()
PARSE_PATH = "/api/v1/intent/parse"


class SlidingWindowLimiter:
    """Per-process sliding 60 s window."""

    def __init__(self, limit: int, window_s: float = 60.0, clock=time.monotonic):
        self._limit, self._window, self._clock = limit, window_s, clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._hits and now - self._hits[0] >= self._window:
                self._hits.popleft()
            if len(self._hits) >= self._limit:
                return False
            self._hits.append(now)
            return True


def check_context_consistency(ctx) -> None:
    """422 invalid_semantic_context (LLD C2 request checks)."""
    bad = lambda why: ParserError("invalid_semantic_context", why)  # noqa: E731
    if not ctx.catalog_version.strip():
        raise bad("catalog_version must not be empty")
    if not 1 <= len(ctx.selected_domains) <= 3:
        raise bad("selected_domains must contain 1 to 3 entries")
    metric_names = [m.name for m in ctx.metrics]
    dim_names = [d.name for d in ctx.dimensions]
    if len(set(metric_names)) != len(metric_names) or len(set(dim_names)) != len(dim_names):
        raise bad("metric and dimension names must be unique")
    known = set(dim_names)
    for m in ctx.metrics:
        if not set(m.allowed_dimensions) <= known:
            raise bad("a metric allows a dimension that is not in the context")
        if m.time_dimension is not None and m.time_dimension not in known:
            raise bad("a metric time_dimension is not in the context")
    if any(has_context_code(t) for t in _context_texts(ctx)):
        raise bad("the context contains SQL or code patterns")


def _context_texts(ctx):
    for m in ctx.metrics:
        yield from (m.name, m.label, m.description, *m.synonyms, *m.clarification_notes)
    for d in ctx.dimensions:
        yield from (d.name, d.label, d.description, *d.synonyms, *d.clarification_notes)
    for g in ctx.glossary:
        yield from (g.term, g.meaning, *g.maps_to, g.clarification_question_template or "")
    for r in ctx.ambiguity_rules:
        yield from (r.trigger, *r.alternatives, r.question_template)
    yield from ctx.response_rules


@router.post("/api/v1/intent/parse", response_model=IntentParserResponse)
def parse(payload: IntentParserRequest, request: Request):
    state, app_state = request.state, request.app.state
    state.request_id = payload.request_id or state.request_id
    state.catalog_version = payload.semantic_context.catalog_version
    state.question = payload.question
    if not payload.question.strip():
        raise ParserError("invalid_request", "The question must not be blank.")
    expected = request.headers.get("x-catalog-version")
    if expected and expected != payload.semantic_context.catalog_version:
        raise ParserError("catalog_version_mismatch", "The catalog version differs from the expected version.")
    check_context_consistency(payload.semantic_context)
    if app_state.client is None:
        raise ParserError("parser_not_ready", not_ready_detail(request.app.state.settings))
    if not app_state.limiter.allow():
        raise ParserError("rate_limited", "Too many requests; retry later.")
    outcome = parse_intent(payload, app_state.client, app_state.metrics,
                           max_repairs=app_state.settings.parser_max_repair_attempts,
                           timeout_s=app_state.settings.parser_llm_timeout_seconds)
    state.outcome = outcome.outcome
    if outcome.outcome == "clarification_required":
        app_state.metrics.inc("intent_parser_clarification_total")
    elif outcome.outcome == "unsupported":
        app_state.metrics.inc("intent_parser_unsupported_total")
    return outcome
