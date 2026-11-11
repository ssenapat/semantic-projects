"""Reference date: QUERY_REFERENCE_DATE if set, otherwise today's date.

"Last month" means the calendar month before the current one. It is never derived from the data
loaded in the database.
"""
from __future__ import annotations

from datetime import date


def resolve_reference_date(configured: str | None = None) -> date:
    if configured and configured.strip():
        return date.fromisoformat(configured.strip())
    return date.today()
