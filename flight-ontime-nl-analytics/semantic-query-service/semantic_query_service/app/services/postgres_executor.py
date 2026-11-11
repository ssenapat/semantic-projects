"""Read-only PostgreSQL executor (REQ-011/012/056/057). Every statement is guarded first (I2).

The password comes only from Settings.pgpassword (env PGPASSWORD); SP-1 ``pg_client`` opens the
read-only session (default_transaction_read_only=on, statement/idle timeouts). Single attempt."""
from __future__ import annotations

import time
from typing import Any, Callable

from ..schemas.errors import (DatabaseUnavailable, QueryExecutionFailed, QueryTimeout, SqlGuardRejection)
from ..sp1_bridge import ensure_sp1_on_path
from . import sql_guard

_TIMEOUT_MARKERS = ("querycanceled", "statement timeout", "canceling statement")


class PostgresExecutor:
    def __init__(self, settings, policy=None, connect: Callable[..., Any] | None = None):
        self.settings = settings
        self.policy = policy
        self._connect = connect
        self.last_db_ms = 0

    def _open(self):
        ensure_sp1_on_path(getattr(self.settings, "semantic_layer_src", None))
        from semantic_layer import pg_client
        pw = self.settings.pgpassword
        if pw is None or not pw.get_secret_value():
            raise DatabaseUnavailable("database password is not configured")
        cs = pg_client.ConnectionSettings(host=self.settings.pghost, port=int(self.settings.pgport),
                                          dbname=self.settings.pgdatabase, user=self.settings.pguser,
                                          password=pw.get_secret_value())
        connect = self._connect or pg_client.connect
        try:
            return connect(cs, self.settings.query_statement_timeout_seconds,
                           self.settings.query_idle_tx_timeout_seconds)
        except Exception:  # noqa: BLE001 - message intentionally dropped (may contain DSN material)
            raise DatabaseUnavailable("cannot connect to the database") from None

    def fetch(self, sql: str, params: dict | None = None) -> list[dict]:
        sql_guard.check(sql, self.policy)          # raises SqlGuardRejection
        started = time.perf_counter()
        with self._open() as conn:
            try:
                res = conn.fetch(sql, params)
            except Exception as exc:  # noqa: BLE001
                text = f"{type(exc).__name__} {exc}".lower()
                if any(m in text for m in _TIMEOUT_MARKERS):
                    raise QueryTimeout("statement timeout") from None
                raise QueryExecutionFailed("database error") from None
        self.last_db_ms = int((time.perf_counter() - started) * 1000)
        cols = list(res.columns)
        return [dict(zip(cols, row)) for row in res.rows]

    def explain_cost(self, sql: str, params: dict | None = None) -> float:
        """EXPLAIN (FORMAT JSON) total cost of an already guarded statement (REQ-061)."""
        sql_guard.check(sql, self.policy)
        with self._open() as conn:
            try:
                raw = getattr(conn, "_conn")   # SP-1 fetch() only accepts SELECT; EXPLAIN uses the same read-only session
                with raw.cursor() as cur:
                    cur.execute("EXPLAIN (FORMAT JSON) " + sql, params or {})
                    plan = cur.fetchone()[0]
                plan = plan[0] if isinstance(plan, list) else plan
                return float(plan["Plan"]["Total Cost"])
            except Exception:  # noqa: BLE001
                raise QueryExecutionFailed("explain failed") from None

    def ping(self) -> None:
        with self._open():
            return None
