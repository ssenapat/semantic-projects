"""Error codes (CONTRACT 5.2) and the safe error envelope."""
from .schemas.response import ErrorEnvelope

ERRORS: dict[str, tuple[int, str]] = {
    "invalid_request": (400, "Invalid request"),
    "catalog_version_mismatch": (409, "Catalog version mismatch"),
    "invalid_semantic_context": (422, "Invalid semantic context"),
    "invalid_model_output": (422, "Invalid model output"),
    "rate_limited": (429, "Rate limited"),
    "llm_provider_failure": (502, "LLM provider failure"),
    "parser_not_ready": (503, "Parser not ready"),
}


class ParserError(Exception):
    def __init__(self, code: str, detail: str, *, status: int | None = None):
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.status = status or ERRORS[code][0]
        self.headers: dict[str, str] = {}


def envelope(code: str, detail: str, request_id: str) -> ErrorEnvelope:
    status, title = ERRORS[code]
    return ErrorEnvelope(type=f"https://example.internal/errors/{code.replace('_', '-')}",
                         title=title, status=status, detail=detail, request_id=request_id)
