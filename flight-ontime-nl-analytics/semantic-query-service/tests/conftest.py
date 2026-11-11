from __future__ import annotations

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

SERVICE = Path(__file__).resolve().parents[1]
DEFS = (SERVICE.parent / "semantic-layer" / "definitions").resolve()

AIRPORTS = [("ORD", "Chicago, IL", "IL"), ("MDW", "Chicago, IL", "IL"), ("JFK", "New York, NY", "NY"),
            ("LGA", "New York, NY", "NY"), ("ATL", "Atlanta, GA", "GA")]


class FakeConn:
    """Stands in for semantic_layer.pg_client.ReadOnlyConnection; records every statement."""

    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def fetch(self, sql, params=None):
        self.owner.statements.append((sql, params))
        if self.owner.error is not None:
            raise self.owner.error
        low = sql.lower()
        if "ontime.airport" in low:
            return SimpleNamespace(columns=["code", "city_name", "state_abr"], rows=list(AIRPORTS))
        if "ontime.carrier" in low:
            return SimpleNamespace(columns=["unique_carrier"], rows=[("UA",), ("AA",), ("DL",)])
        if "max(fl_date)" in low:
            return SimpleNamespace(columns=["m"], rows=[(date(2026, 1, 31),)])
        cols, rows = self.owner.result(sql, params)
        return SimpleNamespace(columns=cols, rows=rows)


class FakeDb:
    def __init__(self):
        self.statements = []
        self.error = None
        self.connect_error = None
        self.passwords = []
        self.result = lambda sql, params: (["flight_count"], [(42,)])

    def connect(self, cs, statement_timeout_s=30, idle_tx_timeout_s=60):
        self.passwords.append(cs.password)
        if self.connect_error is not None:
            raise self.connect_error
        return FakeConn(self)


@pytest.fixture
def db():
    return FakeDb()


@pytest.fixture
def settings(tmp_path):
    from semantic_query_service.app.config import Settings
    return Settings(semantic_layer_definitions_dir=str(DEFS), pgpassword=SecretStr("s3cret-pw"),
                    query_audit_log_path=str(tmp_path / "audit.jsonl"), query_reference_date="2026-01-31")


@pytest.fixture
def executor(settings, db):
    from semantic_query_service.app.services.postgres_executor import PostgresExecutor
    return PostgresExecutor(settings, connect=db.connect)


@pytest.fixture
def client(settings, executor):
    from fastapi.testclient import TestClient
    from semantic_query_service.app.main import create_app
    return TestClient(create_app(settings, executor))


@pytest.fixture
def catalog_version(client):
    return client.get("/health/ready").json()["catalog_version"]


@pytest.fixture
def body(catalog_version):
    def make(**sr):
        sr.setdefault("metrics", ["flight_count"])
        return {"request_id": "r1", "catalog_version": catalog_version, "semantic_request": sr}
    return make
