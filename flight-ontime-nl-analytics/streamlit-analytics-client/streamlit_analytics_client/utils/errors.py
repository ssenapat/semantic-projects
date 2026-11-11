"""User-facing error messages (CONTRACT §6.8)."""
from __future__ import annotations

from typing import Optional


def user_message(status: Optional[int], request_id: Optional[str] = None) -> str:
    if status in (400, 422):
        msg = "Please rephrase your question."
    elif status == 409:
        msg = "The metric catalog was updated. Please ask again."
    elif status in (424, 503):
        msg = "The analytics service is temporarily unavailable. Please try again shortly."
    elif status == 429:
        msg = "Too many requests. Please wait a moment."
    else:
        msg = "Something went wrong."
    if request_id:
        msg += f"\n\nRequest ID: {request_id}"
    return msg
