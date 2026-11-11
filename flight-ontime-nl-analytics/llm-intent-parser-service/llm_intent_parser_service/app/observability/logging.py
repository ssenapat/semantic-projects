"""One JSON log line per request to stdout (CONTRACT 6.9).

Never logs question text, prompt, semantic context, SQL, row values or secrets.
"""
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone

from ..config import SERVICE_NAME, SERVICE_VERSION

_LOGGER_NAME = "intent_parser"


class _StdoutHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:  # resolves sys.stdout at emit time
        sys.stdout.write(record.getMessage() + "\n")
        sys.stdout.flush()


def configure_logging(level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not any(isinstance(h, _StdoutHandler) for h in logger.handlers):
        logger.addHandler(_StdoutHandler())
    logger.propagate = False
    logger.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    return logger


def log_event(*, event: str, request_id: str, catalog_version: str | None = None, outcome: str | None = None,
              latency_ms: float | None = None, level: int = logging.INFO, question: str | None = None,
              error_code: str | None = None) -> None:
    record: dict = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "level": logging.getLevelName(level),
        "service_name": SERVICE_NAME,
        "service_version": SERVICE_VERSION,
        "request_id": request_id,
        "catalog_version": catalog_version,
        "semantic_version": None,  # the Parser has no catalog access; kept for the common trace fields
        "outcome": outcome,
        "latency_ms": None if latency_ms is None else round(latency_ms, 3),
        "event": event,
    }
    if question is not None:
        record["question_sha256"] = hashlib.sha256(question.encode("utf-8")).hexdigest()
        record["question_chars"] = len(question)
    if error_code:
        record["error_code"] = error_code
    logging.getLogger(_LOGGER_NAME).log(level, json.dumps(record, ensure_ascii=False))
