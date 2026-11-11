"""REQ-079: evaluation suites at the integration level (retrieval accuracy; the service suites cover the rest)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TS / "e2e"))
from conftest import ACCEPTANCE  # noqa: E402

# CONTRACT section 6 (normative), run through Retriever -> Parser (--stub-llm == PARSER_LLM_CLIENT=stub).
# (example number, expected /ask outcome, expected column names or None, needs a database for the values)
CANONICAL = [
    (1, "result", ["carrier_code", "origin_airport_code", "cancelled_flight_count"], True),
    (2, "result", ["carrier_code", "flight_count", "cancelled_flight_count", "cancellation_rate"], True),
    (3, "result", ["carrier_code", "origin_airport_code", "flight_count", "cancelled_flight_count"], True),
    (4, "clarification_required", None, False),
    (5, "unsupported", None, False),
    (6, "result", ["flight_count"], True),
]


def test_retrieval_paraphrase_pass_rate():
    env = dict(os.environ, SEMANTIC_LAYER_SRC=str(TS / "semantic-layer" / "src"),
               SEMANTIC_LAYER_DEFINITIONS_DIR=str(TS / "semantic-layer" / "definitions"))
    r = subprocess.run([sys.executable, "scripts/evaluate_retrieval.py", "--min-pass-rate", "0.9"],
                       cwd=TS / "semantic-retrieval-service", env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, (r.stdout + r.stderr)[-500:]


@pytest.mark.parametrize("number,outcome,columns,_needs_db", CANONICAL)
def test_canonical_example_outcome_and_shape(fake_stack, number, outcome, columns, _needs_db):
    """Examples 1-6. The fake DB stands in for Postgres, so only the structure is asserted here (REQ-046);
    the values are asserted by the `db` tests in test_e2e.py."""
    retriever, _query, _db = fake_stack
    r = retriever.post("/api/v1/ask", json={"question": ACCEPTANCE[number]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["outcome"] == outcome
    if columns is not None:
        result = body["result"]
        assert [c["name"] for c in result["columns"]] == columns
        assert isinstance(result["truncated"], bool) and result["row_count"] == len(result["rows"])
    assert "SELECT " not in json.dumps({k: v for k, v in body.items() if k != "result"}).upper()


def test_canonical_example_7_legacy_field_is_rejected(fake_stack):
    _retriever, query, _db = fake_stack
    version = query.get("/health/ready").json()["catalog_version"]
    r = query.post("/api/v1/query/execute", json={
        "request_id": "eval-7", "catalog_version": version,
        "semantic_request": {"metric": "cancelled_flight_count"}})  # legacy-rejection
    assert r.status_code == 422 and r.json()["type"].endswith("/query-validation-failed")


def test_rejected_cases_never_reach_the_database(fake_stack):
    _retriever, query, db = fake_stack
    version = query.get("/health/ready").json()["catalog_version"]
    six = ["flight_count", "cancelled_flight_count", "cancellation_rate", "operated_flight_count", "m5", "m6"]
    for metrics in (["flight_count", "flight_count"], six):   # a duplicate metric; 6 metrics
        r = query.post("/api/v1/query/execute", json={"request_id": "eval-r", "catalog_version": version,
                                                      "semantic_request": {"metrics": metrics}})
        assert r.status_code == 422
    assert db.data_statements() == []
