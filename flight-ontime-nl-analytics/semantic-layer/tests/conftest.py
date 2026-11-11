"""Shared fixtures for the semantic-layer test suite (REQ-024).

Offline tests use the real ``definitions/`` directory, mutated temporary copies of it, and
fake connections. Nothing here opens a database or reads ``data/``. Integration tests
(marker ``integration``) are skipped unless a password env var is set (CONTRACT C-6.3).
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import pytest

from semantic_layer.loader import load_catalog

ROOT = Path(__file__).resolve().parents[1]  # .../semantic-layer
DEFINITIONS = ROOT / "definitions"
VALIDATION = ROOT / "validation"
PASSWORD_VARS = ("TRANSPORT_STATS_PG_PASSWORD", "PGPASSWORD")
SKIP_REASON = (
    "integration test skipped: neither TRANSPORT_STATS_PG_PASSWORD nor PGPASSWORD is set "
    "(a live read-only connection to tran-stats is required)"
)


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: needs a live read-only database connection")


def pytest_collection_modifyitems(config, items):
    if any(os.environ.get(v) for v in PASSWORD_VARS):
        return
    skip = pytest.mark.skip(reason=SKIP_REASON)
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


# --------------------------------------------------------------------------- definitions
@pytest.fixture(scope="session")
def definitions_dir() -> Path:
    return DEFINITIONS


@pytest.fixture(scope="session")
def validation_dir() -> Path:
    return VALIDATION


@pytest.fixture(scope="session")
def catalog():
    return load_catalog(DEFINITIONS)


@pytest.fixture
def mutate_definitions(tmp_path):
    """Return ``mutate(rel_path, old, new, count=1) -> Path``: a fresh temporary copy of
    ``definitions/`` with one textual replacement applied. The originals are never touched.
    Calling it again on the same test builds another independent copy."""
    counter = {"n": 0}

    def mutate(rel: str, old: str, new: str, count: int = 1) -> Path:
        counter["n"] += 1
        dest = tmp_path / f"copy{counter['n']}" / "definitions"
        shutil.copytree(DEFINITIONS, dest)
        target = dest / rel
        text = target.read_text(encoding="utf-8")
        assert old in text, f"mutation anchor {old!r} not found in {rel}"
        target.write_text(text.replace(old, new, count), encoding="utf-8")
        return dest

    return mutate


@pytest.fixture
def copy_definitions(tmp_path):
    dest = tmp_path / "plain_copy" / "definitions"
    shutil.copytree(DEFINITIONS, dest)
    return dest


# --------------------------------------------------------------------------- metric doc
_DOC_LINES = (
    "- Label: {label}",
    "- Status: {status}",
    "- Version: {version}",
    "- Business owner: {business_owner}",
    "- Technical owner: {technical_owner}",
    "- Business definition: {description}",
    "- Source / grain / time basis: {source} / {grain} / {time_basis}",
    "- Formula: {expression}",
    "- Null handling and exclusions: {null_handling}",
    "- Allowed dimensions: see definitions",
    "- Known caveats: none",
    "- Validation SQL and reconciliation: {validation_sql}",
    "- Approval: pending",
    "- Change log: see definitions",
)


def render_metric_doc(catalog) -> str:
    """A minimal docs/metric-catalog.md matching the structure the validator expects."""
    out = ["# Metric catalog", ""]
    for name in sorted(catalog.metrics):
        m = catalog.metrics[name]
        out.append(f"## {m.name}")
        for line in _DOC_LINES:
            out.append(line.format(
                label=m.label, status=m.status, version=m.version,
                business_owner=m.business_owner, technical_owner=m.technical_owner,
                description=m.description, source=m.source, grain=m.grain,
                time_basis=m.time_basis, expression=m.expression,
                null_handling=m.null_handling, validation_sql=m.validation_sql))
        out.append("")
    return "\n".join(out)


@pytest.fixture
def metric_doc(tmp_path, catalog):
    path = tmp_path / "metric-catalog.md"
    path.write_text(render_metric_doc(catalog), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- fake DB
class FakeDescription:
    def __init__(self, name):
        self.name = name


class FakeCursor:
    def __init__(self, conn):
        self._conn = conn
        self.description = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self._conn.executed.append((sql, params))
        if self._conn.fail_with is not None:
            raise self._conn.fail_with
        columns, rows = self._conn.responder(sql, params)
        self.description = [FakeDescription(c) for c in columns] if columns else None
        self._rows = list(rows)

    def fetchall(self):
        return self._rows


class FakeConnection:
    """Stands in for a psycopg connection. ``responder(sql, params) -> (columns, rows)``."""

    def __init__(self, responder=None, fail_with=None):
        self.responder = responder or (lambda sql, params: (["x"], [(1,)]))
        self.fail_with = fail_with
        self.executed = []
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


@pytest.fixture
def fake_connection():
    return FakeConnection


def no_password_env(monkeypatch):
    for var in PASSWORD_VARS:
        monkeypatch.delenv(var, raising=False)


def sql_text_has_no_literals(sql: str) -> bool:
    """True when the SQL contains no quoted string literal (identifiers use double quotes)."""
    return re.search(r"'", sql) is None
