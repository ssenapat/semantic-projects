"""JSON logs, one line per request (CONTRACT 6.9). Never logs question text or secrets."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import sys

SERVICE_NAME = "semantic-retrieval-service"
SERVICE_VERSION = "0.1.0"
_LOGGER = "semantic_retrieval_service"
_ALLOWED = ("timestamp", "level", "service_name", "service_version", "request_id", "catalog_version",
            "semantic_version", "outcome", "latency_ms", "event", "question_sha256", "question_chars",
            "error_code")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "ghe_fields", None) or {"event": "log"}
        return json.dumps(payload, separators=(",", ":"))


class _StdoutHandler(logging.StreamHandler):
    """Always writes to the current sys.stdout."""

    def __init__(self) -> None:
        super().__init__(sys.stdout)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, _value) -> None:
        pass


def configure_logging(level: str = "INFO") -> None:
    logger = logging.getLogger(_LOGGER)
    logger.setLevel(level.upper() if level.upper() in logging._nameToLevel else "INFO")
    if not any(getattr(h, "_ghe", False) for h in logger.handlers):
        h = _StdoutHandler()
        h._ghe = True  # type: ignore[attr-defined]
        h.setFormatter(JsonFormatter())
        logger.addHandler(h)
    logger.propagate = False


def question_fields(question: str) -> dict:
    return {"question_sha256": hashlib.sha256(question.encode("utf-8")).hexdigest(),
            "question_chars": len(question)}


def log_event(event: str, *, request_id: str = "", catalog_version: str = "", semantic_version: str = "",
              outcome: str = "", latency_ms: int = 0, level: str = "INFO", **optional) -> None:
    fields = {
        "timestamp": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "level": level, "service_name": SERVICE_NAME, "service_version": SERVICE_VERSION,
        "request_id": request_id, "catalog_version": catalog_version, "semantic_version": semantic_version,
        "outcome": outcome, "latency_ms": latency_ms, "event": event,
    }
    fields.update({k: v for k, v in optional.items() if k in _ALLOWED and v is not None})
    logging.getLogger(_LOGGER).log(logging.getLevelName(level), "", extra={"ghe_fields": fields})
