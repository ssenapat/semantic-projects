import json
import re
import socket
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llm_intent_parser_service.app.config import Settings
from llm_intent_parser_service.app.main import create_app

_REAL_CONNECT = socket.socket.connect  # captured before the autouse offline guard patches it
CATALOG = "sp1-test0001"
REFERENCE_DATE = "2026-01-31"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def deny(*a, **k):
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(socket.socket, "connect", deny)
    for k in ("ANTHROPIC_API_KEY", "PARSER_LLM_CLIENT", "PARSER_STUB_RESPONSES_FILE", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(k, raising=False)


def allow_loopback(monkeypatch) -> None:
    """Lets a test reach 127.0.0.1 only (local fake endpoints); everything else stays denied."""
    def guarded(sock, address, *a, **k):
        if isinstance(address, tuple) and address[0] == "127.0.0.1":
            return _REAL_CONNECT(sock, address, *a, **k)
        raise AssertionError("network access attempted in an offline test")
    monkeypatch.setattr(socket.socket, "connect", guarded)


def make_context(**overrides) -> dict:
    ctx = {
        "request_id": "req-1", "catalog_version": CATALOG, "selected_domains": ["flight_operations"],
        "metrics": [
            {"name": "cancelled_flight_count", "label": "Cancelled flights", "description": "Number of cancelled flights",
             "domain": "flight_operations", "time_dimension": "flight_date",
             "allowed_dimensions": ["flight_date", "origin_airport_code", "carrier_code"],
             "allowed_time_grains": ["day", "week", "month"], "combinable_with": "flight_operations"},
            {"name": "flight_count", "label": "Flights", "description": "Number of flights from the schedule",
             "domain": "flight_operations", "time_dimension": "flight_date",
             "allowed_dimensions": ["flight_date", "origin_airport_code"], "allowed_time_grains": ["day", "month"], "combinable_with": "flight_operations"},
        ],
        "dimensions": [
            {"name": "flight_date", "label": "Flight date", "description": "Date of flight", "type": "time",
             "allowed_operators": ["between", "equals"], "allowed_time_grains": ["day", "week", "month"]},
            {"name": "origin_airport_code", "label": "Origin airport", "description": "Departure airport",
             "type": "categorical", "allowed_operators": ["equals", "not_equals", "in", "not_in"]},
            {"name": "carrier_code", "label": "Carrier", "description": "Carrier", "type": "categorical",
             "allowed_operators": ["equals", "in"]},
        ],
        "glossary": [], "ambiguity_rules": [], "response_rules": ["Use only supplied names."],
    }
    ctx.update(overrides)
    return ctx


SNAPSHOTS = Path(__file__).parent / "fixtures" / "retriever_contexts"
_NO_CONTEXT_FALLBACK = "show_cancellations_by_airline"  # the Retriever answers 422 for these questions (no snapshot)


def retriever_context(question: str) -> dict:
    """Context snapshot taken from the real Retriever (tests/snapshot_retriever_contexts.py)."""
    slug = re.sub(r"[^a-z0-9]+", "_", question.lower()).strip("_")
    f = SNAPSHOTS / f"{slug}.json"
    if not f.exists():
        f = SNAPSHOTS / f"{_NO_CONTEXT_FALLBACK}.json"
    return json.loads(f.read_text(encoding="utf-8"))["semantic_context"]


def body(question: str, ctx: dict | None = None, **extra) -> dict:
    return {"request_id": "req-1", "question": question, "semantic_context": ctx or make_context(), **extra}


def stub_settings(tmp_path=None, fixtures: list | None = None, **kw) -> Settings:
    kwargs = {"PARSER_LLM_CLIENT": "stub", **kw}
    if fixtures is not None:
        f = tmp_path / "stub.json"
        f.write_text(json.dumps({"fixtures": fixtures}), encoding="utf-8")
        kwargs["PARSER_STUB_RESPONSES_FILE"] = str(f)
    return Settings(**{k.lower(): v for k, v in kwargs.items()})


def fixture_for(question: str, outcome: dict) -> dict:
    return {"questions": [question], "outcome": outcome}


@pytest.fixture
def client():
    return TestClient(create_app(stub_settings()))
