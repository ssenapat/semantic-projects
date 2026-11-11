"""Query audit JSONL (CONTRACT §6.9, REQ-059): no values, SQL or credentials."""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class QueryAuditRepository:
    def __init__(self, path: str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def record(self, *, request_id: str, catalog_version: str, metrics: list[str], metric_versions: dict[str, int],
               group_by: list[str], filter_dimensions: list[str], outcome: str, row_count: int,
               elapsed_ms: int) -> None:
        rec = {"request_id": request_id, "catalog_version": catalog_version, "metrics": list(metrics),
               "metric_versions": dict(metric_versions), "group_by": group_by, "filter_dimensions": filter_dimensions,
               "outcome": outcome, "row_count": row_count, "elapsed_ms": elapsed_ms,
               "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec) + "\n")
        except OSError:
            pass   # auditing must never fail a query
