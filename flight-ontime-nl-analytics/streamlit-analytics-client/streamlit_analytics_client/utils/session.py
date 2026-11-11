from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from ..models import session_models as sm


def format_range(rng) -> Optional[str]:
    """Human text for query_execution.resolved_date_range (end is exclusive), or None."""
    if not isinstance(rng, dict) or not rng.get("start_inclusive"):
        return None
    return f"{rng.get('start_inclusive')} to {rng.get('end_exclusive')} (end exclusive)"


def init_session(state) -> None:
    state.setdefault(sm.KEY_CONVERSATION, [])
    state.setdefault(sm.KEY_HISTORY, [])
    state.setdefault(sm.KEY_LAST_RESPONSE, None)
    state.setdefault(sm.KEY_PENDING, None)
    state.setdefault(sm.KEY_CHART, None)


def add_context(state, text: str) -> None:
    ctx = list(state[sm.KEY_CONVERSATION]) + [text]
    state[sm.KEY_CONVERSATION] = ctx[-sm.MAX_CONVERSATION_ITEMS:]


def add_history(state, question: str, resp: dict) -> None:
    result = resp.get("result") or {}
    meta = result.get("metadata") or {}
    entry = {
        "question": question,
        "outcome": resp.get("outcome"),
        "metrics": list(meta.get("metrics") or []) if resp.get("outcome") == "result" else [],
        "applied_filters": meta.get("applied_filters", []),
        "time_range": format_range(((result.get("query_execution") or {}).get("resolved_date_range"))),
        "row_count": result.get("row_count"),
        "request_id": resp.get("request_id"),
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    hist = list(state[sm.KEY_HISTORY]) + [entry]
    state[sm.KEY_HISTORY] = hist[-sm.MAX_HISTORY_ITEMS:]
