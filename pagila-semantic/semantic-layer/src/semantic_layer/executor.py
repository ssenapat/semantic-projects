"""Runs guarded SQL in a read-only PostgreSQL session (spec 4.3, 14.2)."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import psycopg

from .errors import QueryTimeoutError, SemanticError

log = logging.getLogger("semantic_layer")


@dataclass
class PgConfig:
    host: str
    port: int
    user: str
    password: str
    database: str = "pagila"
    statement_timeout_ms: int = 30_000

    @classmethod
    def from_env(cls) -> "PgConfig":
        missing = [v for v in ("PAGILA_PG_USER", "PAGILA_PG_PASSWORD") if not os.environ.get(v)]
        if missing:
            raise SemanticError(f"missing environment variables: {', '.join(missing)} "
                                "(credentials are read from the environment only)")
        return cls(host=os.environ.get("PAGILA_PG_HOST", "localhost"),
                   port=int(os.environ.get("PAGILA_PG_PORT", "5432")),
                   user=os.environ["PAGILA_PG_USER"],
                   password=os.environ["PAGILA_PG_PASSWORD"],
                   database=os.environ.get("PAGILA_PG_DATABASE", "pagila"),
                   statement_timeout_ms=int(os.environ.get("PAGILA_PG_STATEMENT_TIMEOUT_MS", "30000")))


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


class Executor:
    def __init__(self, config: PgConfig):
        self.config = config
        self._warned = False

    def _connect(self) -> psycopg.Connection:
        c = self.config
        conn = psycopg.connect(host=c.host, port=c.port, user=c.user, password=c.password, dbname=c.database,
                               autocommit=True, connect_timeout=10)
        try:
            conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            conn.execute(f"SET statement_timeout = {int(c.statement_timeout_ms)}")
            conn.execute("SET search_path = public")
            conn.autocommit = False
            conn.read_only = True
            if not self._warned:
                row = conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()
                conn.rollback()
                if row and row[0]:
                    log.warning("connected as a PostgreSQL superuser (%s). Only the read-only session and the SQL guard "
                                "protect this database; this is acceptable for the local sample only.", c.user)
                self._warned = True
        except Exception:
            conn.close()
            raise
        return conn

    def run(self, sql: str, params: list[Any]) -> tuple[list[str], list[list[Any]]]:
        """Execute already-guarded SQL. Returns column names and JSON-friendly rows."""
        with self._connect() as conn:
            try:
                cur = conn.execute(sql, params)
                cols = [d.name for d in cur.description]
                rows = [[_jsonable(v) for v in r] for r in cur.fetchall()]
            except psycopg.errors.QueryCanceled:
                raise QueryTimeoutError(f"query exceeded the {self.config.statement_timeout_ms} ms time limit") from None
            finally:
                conn.rollback()
        return cols, rows
