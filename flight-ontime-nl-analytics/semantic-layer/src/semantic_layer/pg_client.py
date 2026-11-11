"""Read-only PostgreSQL access (CONTRACT C-6). The only module that imports psycopg (lazily).

Credentials come only from environment variables (C-6.2). The password is never printed,
logged, placed in a message, or written anywhere. All statements go through
``ReadOnlyConnection.fetch`` after ``assert_read_only_sql``; every call ends in rollback.
"""
from __future__ import annotations

import dataclasses
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from .errors import ConfigError, DatabaseError
from .policy import assert_read_only_sql

ENV_HOST = "TRANSPORT_STATS_PG_HOST"
ENV_PORT = "TRANSPORT_STATS_PG_PORT"
ENV_DATABASE = "TRANSPORT_STATS_PG_DATABASE"
ENV_USER = "TRANSPORT_STATS_PG_USER"
ENV_PASSWORD = "TRANSPORT_STATS_PG_PASSWORD"

_SETTING_KEYS = (
    "transaction_read_only",
    "default_transaction_read_only",
    "statement_timeout",
    "idle_in_transaction_session_timeout",
)


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value else None


@dataclass(frozen=True)
class ConnectionSettings:
    host: str
    port: int
    dbname: str
    user: str
    password: str = field(repr=False)

    @classmethod
    def from_env(cls, default_database: str) -> "ConnectionSettings":
        """Build settings from env vars only; raises ConfigError naming missing variables."""
        user = _env(ENV_USER) or _env("PGUSER")
        password = _env(ENV_PASSWORD) or _env("PGPASSWORD")
        problems = []
        if user is None:
            problems.append(f"No database user: set {ENV_USER} (or PGUSER) in the environment")
        if password is None:
            problems.append(
                f"No database password: set PGPASSWORD (or {ENV_PASSWORD}) in the environment"
            )
        if problems:
            raise ConfigError("; ".join(problems))
        raw_port = _env(ENV_PORT) or "5432"
        try:
            port = int(raw_port)
        except ValueError:
            raise ConfigError(f"{ENV_PORT} must be an integer") from None
        return cls(
            host=_env(ENV_HOST) or "localhost",
            port=port,
            dbname=_env(ENV_DATABASE) or default_database,
            user=user,  # type: ignore[arg-type]
            password=password,  # type: ignore[arg-type]
        )

    def with_database(self, dbname: str) -> "ConnectionSettings":
        return dataclasses.replace(self, dbname=dbname)


def build_connect_kwargs(
    settings: ConnectionSettings, statement_timeout_s: int = 30, idle_tx_timeout_s: int = 60
) -> dict:
    """psycopg connect kwargs with read-only session defaults (C-6.3)."""
    statement_timeout_s = int(statement_timeout_s)
    idle_tx_timeout_s = int(idle_tx_timeout_s)
    return {
        "host": settings.host,
        "port": settings.port,
        "dbname": settings.dbname,
        "user": settings.user,
        "password": settings.password,
        "passfile": os.devnull,
        "connect_timeout": 10,
        "application_name": "semantic_layer",
        "options": (
            "-c default_transaction_read_only=on "
            f"-c statement_timeout={statement_timeout_s}s "
            f"-c idle_in_transaction_session_timeout={idle_tx_timeout_s}s"
        ),
    }


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    row_count: int
    duration_ms: int


def _scrub(message: str, password: str | None = None) -> str:
    """Remove any password / DSN material from an error message."""
    if password:
        message = message.replace(password, "***")
    message = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "<dsn removed>", message, flags=re.I)
    message = re.sub(r"(password|passfile)\s*=\s*\S+", r"\1=***", message, flags=re.I)
    return message


def _wrap(exc: BaseException, password: str | None = None) -> DatabaseError:
    return DatabaseError(f"{type(exc).__name__}: {_scrub(str(exc), password)}")


class ReadOnlyConnection:
    """Context manager around a read-only psycopg connection; closes on exit."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def __enter__(self) -> "ReadOnlyConnection":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - closing must not mask the original error
                pass

    def fetch(self, sql: str, params: dict | None = None) -> QueryResult:
        """assert_read_only_sql -> execute(sql, params or {}) -> fetchall -> rollback."""
        assert_read_only_sql(sql)
        if self._conn is None:
            raise DatabaseError("connection is closed")
        started = time.perf_counter()
        try:
            try:
                with self._conn.cursor() as cur:
                    cur.execute(sql, params or {})
                    columns = [d.name for d in (cur.description or [])]
                    rows = [tuple(r) for r in cur.fetchall()] if cur.description else []
            finally:
                self._conn.rollback()
        except Exception as exc:  # noqa: BLE001 - re-raised as DatabaseError, scrubbed
            raise _wrap(exc) from None
        duration_ms = int((time.perf_counter() - started) * 1000)
        return QueryResult(columns=columns, rows=rows, row_count=len(rows), duration_ms=duration_ms)

    def session_settings(self) -> dict[str, str]:
        """transaction_read_only, default_transaction_read_only, statement_timeout,
        idle_in_transaction_session_timeout (via SELECT current_setting(...))."""
        select_list = ", ".join(f"current_setting('{k}') AS {k}" for k in _SETTING_KEYS)
        result = self.fetch(f"SELECT {select_list}")
        return {k: str(v) for k, v in zip(result.columns, result.rows[0])}


def connect(
    settings: ConnectionSettings, statement_timeout_s: int = 30, idle_tx_timeout_s: int = 60
) -> ReadOnlyConnection:
    """Open a read-only session (autocommit off, read_only=True). Raises DatabaseError."""
    try:
        import psycopg  # lazy: only this module imports psycopg (I13)
    except ImportError:
        raise DatabaseError("psycopg is not installed; install semantic-layer dependencies") from None
    kwargs = build_connect_kwargs(settings, statement_timeout_s, idle_tx_timeout_s)
    try:
        conn = psycopg.connect(**kwargs, autocommit=False)
    except Exception as exc:  # noqa: BLE001
        raise _wrap(exc, settings.password) from None
    try:
        conn.read_only = True
    except Exception as exc:  # noqa: BLE001
        conn.close()
        raise _wrap(exc, settings.password) from None
    return ReadOnlyConnection(conn)
