"""ErrorEnvelope (CONTRACT §3) and the Query Service's internal exceptions (§5.3, §7.3)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str        # f"https://example.internal/errors/{code.replace('_', '-')}"
    title: str
    status: int
    detail: str      # safe text: no SQL, stack trace, credential, physical name
    request_id: str


class SqlGuardRejection(Exception):
    """Compiled SQL failed the guard (internal defect). The message never carries SQL."""


class QueryTimeout(Exception):
    pass


class QueryExecutionFailed(Exception):
    pass


class DatabaseUnavailable(Exception):
    pass


ERROR_TABLE = {  # code -> (status, title)
    "catalog_version_mismatch": (409, "Catalog version mismatch"),
    "query_validation_failed": (422, "Query request validation failed"),
    "sql_guard_rejected": (422, "Compiled query rejected by guard"),
    "query_execution_failed": (500, "Query execution failed"),
    "catalog_not_ready": (503, "Catalog not ready"),
    "database_unavailable": (503, "Database unavailable"),
    "query_timeout": (504, "Query timed out"),
}


def make_envelope(code: str, request_id: str, detail: str) -> ErrorEnvelope:
    status, title = ERROR_TABLE[code]
    return ErrorEnvelope(type=f"https://example.internal/errors/{code.replace('_', '-')}",
                         title=title, status=status, detail=detail, request_id=request_id)
