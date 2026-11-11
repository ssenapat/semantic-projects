"""Query Service request/response models (CONTRACT 4.3)."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from .ask import AuthorizationContext
from .parser import ClarificationDetails, ConstrainedSemanticRequest, UnsupportedDetails


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class QueryExecuteRequest(_M):
    request_id: str
    catalog_version: str
    semantic_request: ConstrainedSemanticRequest
    authorization_context: AuthorizationContext = Field(default_factory=AuthorizationContext)


class ResultColumn(_M):
    name: str
    role: Literal["dimension", "metric"]
    label: str
    unit: Literal["count", "ratio", "percent"] | None
    semantic_type: Literal["dimension", "time_dimension", "metric"]
    data_type: str


class ChartHint(_M):
    chart_type: Literal["line", "bar", "stacked_bar", "area", "scatter", "table", "kpi"]
    x_field: str | None = None
    y_fields: list[str] = Field(default_factory=list)
    color_field: str | None = None
    title: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)


class QueryResultMetadata(_M):
    metrics: list[str]
    catalog_version: str
    time_basis: str | None
    applied_filters: list[dict]
    generated_at_utc: str


class QueryResultResponse(_M):
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


class QueryClarificationResponse(_M):
    outcome: Literal["clarification_required"]
    request_id: str
    clarification: ClarificationDetails


class QueryUnsupportedResponse(_M):
    outcome: Literal["unsupported"]
    request_id: str
    unsupported: UnsupportedDetails


QueryResponse = Annotated[QueryResultResponse | QueryClarificationResponse | QueryUnsupportedResponse,
                          Field(discriminator="outcome")]
query_response_adapter: TypeAdapter = TypeAdapter(QueryResponse)
