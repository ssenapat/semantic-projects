"""In-memory stand-in for the read-only Postgres session (no network, no secrets).

It lets the stub-LLM e2e run the real Retriever, Parser, Query Service and semantic-layer compiler without a
database. Values are synthetic and deterministic; real values are asserted by the `db` tests.
"""
from __future__ import annotations

import contextlib
import itertools
import re
from datetime import date
from types import SimpleNamespace

FAKE_DIMENSION_VALUES = {
    "carrier_code": ["AA", "DL", "UA"],
    "origin_airport_code": ["JFK", "MDW", "ORD"],
}
FAKE_AIRPORTS = [("ORD", "Chicago, IL", "IL"), ("MDW", "Chicago, IL", "IL"), ("JFK", "New York, NY", "NY"),
                 ("LGA", "New York, NY", "NY"), ("ATL", "Atlanta, GA", "GA")]


def _fake_rows(aliases: list[str], metric_aliases: set[str], limit: int):
    """Deterministic rows for the SELECT list `aliases`: the dimension product in ascending order, with values."""
    dims = [a for a in aliases if a not in metric_aliases]
    pools = [FAKE_DIMENSION_VALUES.get(d, [f"{d}_{i}" for i in range(3)]) for d in dims]
    rows = []
    for n, combo in enumerate(itertools.product(*pools) if dims else [()]):
        values = dict(zip(dims, combo))
        for a in aliases:
            if a in metric_aliases:
                values[a] = round(0.01 * (n % 7 + 1), 4) if a.endswith("rate") else 1000 - 10 * n
        rows.append(tuple(values[a] for a in aliases))
    return rows[:limit]


class FakeConn:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def fetch(self, sql, params=None):
        self.owner.statements.append((sql, params))
        low = sql.lower()
        if "ontime.airport" in low:
            return SimpleNamespace(columns=["code", "city_name", "state_abr"], rows=list(FAKE_AIRPORTS))
        if "ontime.carrier" in low:
            return SimpleNamespace(columns=["unique_carrier"], rows=[("UA",), ("AA",), ("DL",)])
        if "max(fl_date)" in low:
            return SimpleNamespace(columns=["m"], rows=[(date(2026, 1, 31),)])
        aliases = re.findall(r'AS "([a-z_]+)"', sql.split("\nFROM", 1)[0])
        metrics = {a for a in aliases if a in ("flight_count", "cancelled_flight_count", "cancellation_rate")}
        limit = (params or {}).get("limit", 1000)
        return SimpleNamespace(columns=aliases, rows=_fake_rows(aliases, metrics, limit))


class FakeDb:
    def __init__(self):
        self.statements = []

    def connect(self, cs, statement_timeout_s=30, idle_tx_timeout_s=60):
        return FakeConn(self)

    def data_statements(self):
        return [s for s in self.statements if "ontime.airport" not in s[0].lower()
                and "ontime.carrier" not in s[0].lower() and "max(fl_date)" not in s[0].lower()
                and not s[0].lstrip().upper().startswith("EXPLAIN")]


@contextlib.contextmanager
def open_fake_stack(audit_log_path):
    """Real Retriever, Parser (stub LLM) and Query Service wired in-process over the fake DB.

    Yields (retriever, parser, query, db), each service a started TestClient.
    """
    import os
    from pathlib import Path

    from fastapi.testclient import TestClient
    from pydantic import SecretStr

    ts = Path(__file__).resolve().parents[1]
    os.environ.setdefault("SEMANTIC_LAYER_SRC", str(ts / "semantic-layer" / "src"))
    os.environ.setdefault("SEMANTIC_LAYER_DEFINITIONS_DIR", str(ts / "semantic-layer" / "definitions"))
    os.environ["PARSER_LLM_CLIENT"] = "stub"
    from llm_intent_parser_service.app.config import Settings as ParserSettings
    from llm_intent_parser_service.app.main import create_app as create_parser
    from semantic_query_service.app.config import Settings as QuerySettings
    from semantic_query_service.app.main import create_app as create_query
    from semantic_query_service.app.services.postgres_executor import PostgresExecutor
    from semantic_retrieval_service.app.config import Settings as RetrieverSettings
    from semantic_retrieval_service.app.main import create_app as create_retriever
    from semantic_retrieval_service.app.services.parser_client import ParserClient
    from semantic_retrieval_service.app.services.query_service_client import QueryServiceClient

    db = FakeDb()
    settings = QuerySettings(semantic_layer_definitions_dir=str(ts / "semantic-layer" / "definitions"),
                             pgpassword=SecretStr("unused"), query_audit_log_path=str(audit_log_path),
                             query_reference_date=None)
    with contextlib.ExitStack() as stack:
        query = stack.enter_context(TestClient(create_query(settings, PostgresExecutor(settings, connect=db.connect))))
        parser = stack.enter_context(TestClient(create_parser(ParserSettings())))
        app = create_retriever(RetrieverSettings(), parser_client=ParserClient("http://127.0.0.1:8081", 25, client=parser),
                               query_client=QueryServiceClient("http://127.0.0.1:8082", 40, client=query))
        retriever = stack.enter_context(TestClient(app))
        yield retriever, parser, query, db
