from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)        # tighter limit is applied from settings
    previous_request: dict[str, Any] | None = None             # the request from the last turn, for follow-ups
