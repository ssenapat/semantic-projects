"""Result models (CONTRACT C-2.5)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ResultColumn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    role: Literal["dimension", "metric"]
    label: str
    unit: Literal["count", "ratio", "percent"] | None = None
    semantic_type: Literal["dimension", "time_dimension", "metric"]
    data_type: str


class ChartHint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chart_type: Literal["line", "bar", "stacked_bar", "area", "scatter", "table", "kpi"]
    x_field: str | None = None
    y_fields: list[str] = Field(default_factory=list)
    color_field: str | None = None
    title: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class QueryResultMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[str]
    catalog_version: str
    time_basis: str | None = None
    applied_filters: list[dict] = Field(default_factory=list)
    generated_at_utc: str


class QueryResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["result"]
    request_id: str
    columns: list[ResultColumn]
    rows: list[list]
    truncated: bool
    row_count: int = Field(ge=0)
    metric_versions: dict[str, int]
    metadata: QueryResultMetadata
    chart_hints: list[ChartHint]
    query_execution: dict
