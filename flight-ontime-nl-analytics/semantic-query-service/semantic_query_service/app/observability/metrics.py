"""In-process counters rendered as `name value` text (CONTRACT §6.9)."""
from __future__ import annotations

import threading

NAMES = ("query_service_requests_total", "query_service_validation_failures_total",
         "query_service_clarification_total", "query_service_sql_guard_rejections_total",
         "query_service_db_latency_ms", "query_service_result_rows_total")


class Metrics:
    def __init__(self):
        self._v = {n: 0 for n in NAMES}
        self._lock = threading.Lock()

    def inc(self, name: str, amount: float = 1) -> None:
        with self._lock:
            self._v[name] += amount

    def set(self, name: str, value: float) -> None:
        with self._lock:
            self._v[name] = value

    def get(self, name: str):
        return self._v[name]

    def render(self) -> str:
        with self._lock:
            return "".join(f"{n} {self._v[n]}\n" for n in NAMES)
