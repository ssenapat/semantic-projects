"""Internal plan models (CONTRACT §7.3)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ResolvedDateRange(BaseModel):
    start_inclusive: str
    end_exclusive: str
    time_basis: str


class ValidatedQueryPlan(BaseModel):
    request_id: str
    catalog_version: str
    metrics: list[str]                                           # request order
    metric_versions: dict[str, int] = Field(default_factory=dict)  # same keys, same order
    source_model: str
    group_by: list[dict] = Field(default_factory=list)          # [{"dimension","time_grain","sp1_term"}]
    resolved_filters: list[dict[str, Any]] = Field(default_factory=list)  # [{"dimension","operator","value"}]
    resolved_date_range: ResolvedDateRange | None = None
    mandatory_filters: list[str] = Field(default_factory=list)
    requested_limit: int                                         # already = effective_limit
