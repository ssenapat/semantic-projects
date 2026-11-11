"""REQ-054/055/080: writes and DDL are rejected by the Query guard; the executor session is read-only."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

TS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TS / "semantic-query-service"))
os.environ.setdefault("SEMANTIC_LAYER_SRC", str(TS / "semantic-layer" / "src"))

WRITES = [
    "INSERT INTO ontime.flight (x) VALUES (1)",
    "UPDATE ontime.flight SET x = 1",
    "DELETE FROM ontime.flight",
    "DROP TABLE ontime.flight",
    "CREATE TABLE t (a int)",
    "TRUNCATE ontime.flight",
    "SELECT 1; DELETE FROM ontime.flight",
    "COPY ontime.flight TO '/tmp/x'",
]


@pytest.mark.parametrize("sql", WRITES)
def test_guard_rejects_writes(sql):
    from semantic_query_service.app.schemas.errors import SqlGuardRejection
    from semantic_query_service.app.services import sql_guard

    with pytest.raises(SqlGuardRejection):
        sql_guard.check(sql)


def test_executor_session_is_read_only():
    src = (TS / "semantic-query-service" / "semantic_query_service" / "app" / "services" / "postgres_executor.py").read_text()
    assert "default_transaction_read_only" in src


def test_only_query_service_touches_the_database():
    for folder, pkg in (("semantic-retrieval-service", "semantic_retrieval_service"),
                        ("llm-intent-parser-service", "llm_intent_parser_service"),
                        ("streamlit-analytics-client", "streamlit_analytics_client")):
        for py in (TS / folder / pkg).rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            assert "psycopg" not in text and "import sqlalchemy" not in text, py
