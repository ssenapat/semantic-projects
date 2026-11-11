"""ErrorEnvelope (REQ-009) and the section 5.1 error codes."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

TITLES = {
    "invalid_request": "Invalid request",
    "catalog_version_mismatch": "Catalog version mismatch",
    "no_retrievable_context": "No retrievable context",
    "intent_parser_unavailable": "Intent parser unavailable",
    "query_service_unavailable": "Query service unavailable",
    "rate_limited": "Rate limited",
    "catalog_not_ready": "Catalog not ready",
}
STATUS = {
    "invalid_request": 400, "catalog_version_mismatch": 409, "no_retrievable_context": 422,
    "intent_parser_unavailable": 424, "query_service_unavailable": 424,
    "rate_limited": 429, "catalog_not_ready": 503,
}


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    title: str
    status: int
    detail: str
    request_id: str


class ApiError(Exception):
    """Raised anywhere in the request path; rendered as an ErrorEnvelope."""

    def __init__(self, code: str, detail: str, request_id: str = ""):
        super().__init__(code)
        self.code = code
        self.status = STATUS[code]
        self.detail = detail
        self.request_id = request_id

    def envelope(self) -> ErrorEnvelope:
        return ErrorEnvelope(type="https://example.internal/errors/" + self.code.replace("_", "-"),
                             title=TITLES[self.code], status=self.status, detail=self.detail,
                             request_id=self.request_id)
