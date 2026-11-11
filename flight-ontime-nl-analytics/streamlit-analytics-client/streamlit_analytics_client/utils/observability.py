"""In-session UI counters with JSON log lines (CONTRACT §6.9). Never logs question text."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Optional

from ..models import session_models as sm

SERVICE_NAME = "streamlit-analytics-client"
SERVICE_VERSION = "0.1.0"


def _emit(event: str, counter: str, value: int, request_id: Optional[str], catalog_version: Optional[str],
          semantic_version: Optional[str], outcome: Optional[str]) -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "level": "INFO",
        "service_name": SERVICE_NAME,
        "service_version": SERVICE_VERSION,
        "request_id": request_id,
        "catalog_version": catalog_version,
        "semantic_version": semantic_version,
        "outcome": outcome,
        "latency_ms": None,
        "event": event,
        "counter": counter,
        "value": value,
    }
    print(json.dumps(record), file=sys.stdout, flush=True)


def increment(state, counter: str, event: str, *, request_id: Optional[str] = None,
              catalog_version: Optional[str] = None, semantic_version: Optional[str] = None,
              outcome: Optional[str] = None) -> int:
    value = int(state.get(counter, 0)) + 1
    state[counter] = value
    _emit(event, counter, value, request_id, catalog_version, semantic_version, outcome)
    return value


def count_question(state) -> int:
    return increment(state, sm.COUNTER_QUESTIONS, "user_question")


def _versions(result: dict) -> Optional[str]:
    mv = result.get("metric_versions") or {}
    return ",".join(f"{m}:{v}" for m, v in mv.items()) or None


def count_chart_render(state, resp: dict) -> int:
    result = resp.get("result") or {}
    meta = result.get("metadata") or {}
    return increment(state, sm.COUNTER_CHARTS, "chart_render", request_id=resp.get("request_id"),
                     catalog_version=resp.get("catalog_version") or meta.get("catalog_version"),
                     semantic_version=_versions(result), outcome=resp.get("outcome"))
