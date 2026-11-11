"""The parsing loop: ask Claude for a request, have the semantic layer check it, repair on refusal."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import date
from typing import Any, Callable

from .llm import LLMClient, LLMError, LLMTurn
from .prompt import sanitize_question, system_prompt, tools
from .query_client import QueryRefused, QueryServiceClient, UpstreamError
from .settings import Settings

log = logging.getLogger("intent_parser")
audit = logging.getLogger("intent_parser.audit")


class IntentParser:
    def __init__(self, llm: LLMClient, queries: QueryServiceClient, settings: Settings,
                 today: Callable[[], date] = date.today, clock: Callable[[], float] = time.monotonic,
                 catalog_ttl: float = 300.0):
        self.llm, self.queries, self.settings = llm, queries, settings
        self._today, self._clock, self._ttl = today, clock, catalog_ttl
        self._lock = threading.Lock()
        self._cached: tuple[float, dict, dict, str, list[dict]] | None = None

    # ------------------------------------------------------------------ catalog cache
    def _world(self, request_id: str | None):
        with self._lock:
            now = self._clock()
            if self._cached is None or now - self._cached[0] > self._ttl:
                catalog = self.queries.catalog(request_id)
                context = self.queries.context(request_id)
                policy = context.get("policy")
                if not isinstance(policy, dict) or not all(
                        isinstance(policy.get(k), int) and policy[k] > 0
                        for k in ("max_measures_per_query", "max_dimensions_per_query", "max_rows", "default_limit")):
                    # fail closed: never guess the limits the query service enforces
                    raise UpstreamError("query service context has no usable policy")
                self._cached = (now, catalog, context, system_prompt(catalog, policy), tools(catalog, policy))
            _, catalog, context, system, tool_defs = self._cached
            return context, system, tool_defs

    # ------------------------------------------------------------------ parse
    def parse(self, question: str, previous_request: dict | None = None, request_id: str | None = None) -> dict:
        started = time.perf_counter()
        question = sanitize_question(question)
        context, system, tool_defs = self._world(request_id)
        messages = [{"role": "user", "content": self._user_message(question, previous_request, context)}]
        usage = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0}
        blank: dict[str, Any] = {"status": "failed", "request": None, "assumptions": [], "clarification": None,
                                 "reason": None, "caveats": [], "definitions_used": None}
        result = dict(blank)
        try:
            for attempt in range(1, self.settings.max_llm_calls + 1):
                turn = self.llm.complete(system, messages, tool_defs)
                self._count(usage, turn)
                outcome, feedback = self._interpret(turn, request_id)
                result = {**blank, **outcome}                # each attempt replaces the last
                if feedback is None:
                    break
                messages += [turn.assistant_message(),
                             {"role": "user", "content": [{"type": "tool_result", "tool_use_id": turn.tool_use_id,
                                                           "is_error": True, "content": feedback}]}]
            else:
                result["reason"] = f"could not build a valid request: {result['reason']}"
            if result["status"] == "failed" and not result["reason"]:
                result["reason"] = "no answer was produced"
        finally:
            result["usage"] = usage
            self._audit(question, previous_request, result, started, request_id)
        return result

    def _interpret(self, turn: LLMTurn, request_id: str | None) -> tuple[dict, str | None]:
        """Returns (fields to merge into the result, repair feedback or None when finished)."""
        name, data = turn.tool_name, turn.tool_input
        if name == "not_supported":
            return ({"status": "not_supported", "reason": _text(data.get("reason"), 400) or "not supported",
                     "request": None, "assumptions": []}, None)
        if name == "ask_clarification":
            q = _text(data.get("question"), 400)
            opts = [_text(o, 120) for o in (data.get("options") or []) if isinstance(o, str)][:5]
            if not q:
                return ({"status": "failed", "reason": "the model asked an empty question"}, None)
            return ({"status": "needs_clarification", "clarification": {"question": q, "options": opts},
                     "request": None, "assumptions": []}, None)
        if name != "submit_request":
            return ({"status": "failed", "reason": "the model did not choose a valid action"}, None)

        assumptions = [_text(a, 300) for a in (data.get("assumptions") or []) if isinstance(a, str)][:8]
        request = {k: v for k, v in data.items() if k != "assumptions"}
        try:
            compiled = self.queries.compile(request, request_id)
        except QueryRefused as exc:
            if exc.kind == "unsupported":                 # a final answer, not a mistake to repair
                return ({"status": "not_supported", "reason": exc.message, "request": None,
                         "assumptions": []}, None)
            return ({"status": "failed", "reason": exc.message, "request": None},
                    f"The semantic layer refused this request: {exc.message}. Fix it using only names from the "
                    "catalog, or call ask_clarification or not_supported.")
        return ({"status": "ready", "request": request, "assumptions": assumptions, "reason": None,
                 "clarification": None, "caveats": compiled["caveats"],
                 "definitions_used": compiled["definitions_used"]}, None)

    # ------------------------------------------------------------------ helpers
    def _user_message(self, question: str, previous: dict | None, context: dict) -> str:
        parts = [f"Today is {self._today().isoformat()}. The data ends on {context['data_end']}.",
                 f"<question>{question}</question>"]
        if previous:
            parts.append(f"<previous_request>{json.dumps(previous, separators=(',', ':'))[:4000]}</previous_request>")
        return "\n".join(parts)

    @staticmethod
    def _count(usage: dict, turn: LLMTurn) -> None:
        usage["llm_calls"] += 1
        usage["input_tokens"] += turn.input_tokens
        usage["output_tokens"] += turn.output_tokens
        usage["cache_read_tokens"] += turn.cache_read_tokens

    def _audit(self, question: str, previous: dict | None, result: dict, started: float, request_id: str | None):
        entry = {"request_id": request_id, "question_sha256": hashlib.sha256(question.encode()).hexdigest()[:16],
                 "question_chars": len(question), "follow_up": previous is not None, "status": result["status"],
                 "request": result["request"], "usage": result["usage"], "model": self.settings.model,
                 "duration_ms": round((time.perf_counter() - started) * 1000, 1)}
        if self.settings.log_questions:
            entry["question"] = question
        audit.info(json.dumps(entry, default=str))


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""
