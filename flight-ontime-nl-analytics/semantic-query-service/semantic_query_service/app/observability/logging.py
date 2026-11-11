"""One JSON log line per request to stdout (CONTRACT §6.9). Never logs question text, SQL, values or secrets."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

SERVICE_NAME = "semantic-query-service"
_KEYS = ("request_id", "catalog_version", "semantic_version", "outcome", "latency_ms", "event", "error_code")


def log_event(level: str = "INFO", *, service_version: str = "0.1.0", stream=None, **fields) -> dict:
    rec = {"timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "level": level,
           "service_name": SERVICE_NAME, "service_version": service_version}
    for k in _KEYS:
        if k in fields and fields[k] is not None:
            rec[k] = fields[k]
    (stream or sys.stdout).write(json.dumps(rec) + "\n")
    return rec
