"""Test doubles shared by the intent-parser tests."""
import copy
import threading

from intent_parser.llm import LLMError, LLMTurn


def submit(**request) -> LLMTurn:
    return LLMTurn("submit_request", request, f"toolu_{id(request)}", input_tokens=1000, output_tokens=60, cache_read_tokens=900)


def clarify(question="Which one?", options=None) -> LLMTurn:
    return LLMTurn("ask_clarification", {"question": question, "options": options or []}, "toolu_c", 800, 30)


def unsupported(reason="not available") -> LLMTurn:
    return LLMTurn("not_supported", {"reason": reason}, "toolu_n", 800, 20)


class ScriptedLLM:
    """Plays back prepared turns and records what the parser sent."""

    def __init__(self, *turns):
        self.turns = list(turns)
        self.calls = []                       # (system, messages snapshot, tools)
        self.gate = None
        self.entered = threading.Event()

    def complete(self, system, messages, tools):
        self.calls.append((system, copy.deepcopy(messages), tools))
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(5)
        assert self.turns, "the parser called the model more often than the test scripted"
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


class FakeExecutor:
    def __init__(self):
        self.calls = []

    def run(self, sql, params):
        self.calls.append(sql)
        if "MAX(" in sql:
            return ["max"], [["2026-07-28"]]
        return ["total_revenue"], [[170962.39]]


__all__ = ["submit", "clarify", "unsupported", "ScriptedLLM", "FakeExecutor", "LLMError"]
