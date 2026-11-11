"""Light pydantic mirrors of AskResponse, MetricCatalogResponse, ErrorEnvelope (CONTRACT §3, §4.1)."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, StrictBool, StrictInt


class ErrorEnvelope(BaseModel):
    type: str
    title: str
    status: int
    detail: str
    request_id: str
    code: Optional[str] = None


class AskResponse(BaseModel):
    request_id: str
    catalog_version: str
    authorization_mode: str
    outcome: str
    clarification: Optional[Dict[str, Any]] = None
    unsupported: Optional[Dict[str, Any]] = None
    result: Optional[Dict[str, Any]] = None
    retrieval_summary: Dict[str, Any] = Field(default_factory=dict)


class ResultColumn(BaseModel):
    name: str
    role: Literal["dimension", "metric"]
    label: str
    unit: Optional[str] = None
    semantic_type: Optional[str] = None
    data_type: Optional[str] = None


class QueryResult(BaseModel):
    """Client model of the C-2.5 QueryResultResponse (extra fields are ignored; required ones must be valid)."""
    outcome: str
    request_id: str
    columns: List[ResultColumn]
    rows: List[List[Any]]
    truncated: StrictBool
    row_count: StrictInt
    metric_versions: Dict[str, StrictInt]
    metadata: Dict[str, Any] = Field(default_factory=dict)
    chart_hints: List[Dict[str, Any]] = Field(default_factory=list)
    query_execution: Dict[str, Any] = Field(default_factory=dict)


class MetricDimensionRef(BaseModel):
    name: str
    label: str


class MetricDefinition(BaseModel):
    name: str
    label: str
    description: str = ""
    unit: Optional[str] = None
    combinable_with: Optional[str] = None
    status: str = ""
    time_basis: Optional[str] = None
    allowed_dimensions: List[MetricDimensionRef] = Field(default_factory=list)
    allowed_time_grains: List[str] = Field(default_factory=list)
    synonyms: List[str] = Field(default_factory=list)
    clarification_notes: List[str] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)


class MetricCatalogResponse(BaseModel):
    catalog_version: str
    metrics: List[MetricDefinition] = Field(default_factory=list)
