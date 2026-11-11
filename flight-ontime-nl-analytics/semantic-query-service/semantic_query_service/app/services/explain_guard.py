"""Optional EXPLAIN cost guard (REQ-061; QUERY_EXPLAIN_ENABLED, off by default)."""
from __future__ import annotations

from typing import Callable

from ..schemas.errors import SqlGuardRejection


def check_cost(explain: Callable[[str, dict | None], float], sql: str, params: dict | None, max_cost: float) -> bool:
    """Return True when the plan was checked and is within budget; raise SqlGuardRejection otherwise."""
    cost = explain(sql, params)
    if cost > max_cost:
        raise SqlGuardRejection("explain guard: estimated plan cost exceeds the configured maximum")
    return True
