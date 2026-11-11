"""Real Claude, real compiler, real database. Skipped unless INTENT_MODEL, model access (AWS or ANTHROPIC_API_KEY) and PAGILA_PG_* are set.

    pytest -m live -q

Each golden case checks the structure of the request, not the exact wording of assumptions.
"""
import json
from pathlib import Path

import pytest

CASES = json.loads((Path(__file__).parent.parent / "evals" / "golden.json").read_text())
pytestmark = pytest.mark.live


@pytest.mark.parametrize("case", CASES, ids=[c["q"][:50] for c in CASES])
def test_golden(live_parser, case):
    r = live_parser.parse(case["q"])
    assert r["status"] == case["status"], r
    if case["status"] != "ready":
        return
    req = r["request"]
    assert req["model"] == case["model"]
    assert set(case["measures"]) == set(req["measures"])
    if "dimensions" in case:
        assert set(case["dimensions"]) == set(req.get("dimensions", []))
    if "limit" in case:
        assert req.get("limit") == case["limit"]
    for dim in case.get("filters_on", []):
        assert any(f["dimension"] == dim for f in req.get("filters", []))
    assert r["assumptions"], "defaults must be stated"
