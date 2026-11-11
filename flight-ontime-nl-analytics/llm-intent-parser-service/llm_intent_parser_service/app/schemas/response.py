"""Parser outcome models and the error envelope (CONTRACT 3 and 4.2)."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..policy_limits import MAX_METRICS


class ErrorEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str
    title: str
    status: int
    detail: str
    request_id: str


RelativePeriod = Literal["today", "yesterday", "last_7_days", "last_30_days",
    "last_complete_calendar_month", "last_3_complete_months", "last_6_complete_months",
    "quarter_to_date", "year_to_date"]


class SemanticGroupBy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None


class SemanticFilter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dimension: str
    operator: Literal["equals", "not_equals", "in", "not_in", "between", "contains"]
    values: list[str] = Field(min_length=1, max_length=50)


class SemanticDateRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["absolute", "relative"]
    start_date: str | None = None
    end_date: str | None = None
    relative_period: RelativePeriod | None = None


class ConstrainedSemanticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[str] = Field(min_length=1, max_length=MAX_METRICS)
    group_by: list[SemanticGroupBy] = Field(default_factory=list, max_length=8)
    filters: list[SemanticFilter] = Field(default_factory=list, max_length=20)
    date_range: SemanticDateRange | None = None
    requested_limit: int = Field(default=500, ge=1, le=5000)

    @field_validator("metrics")
    @classmethod
    def _metrics_unique(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("metrics must be unique")
        return v

    @field_validator("group_by")
    @classmethod
    def _group_by_unique(cls, v: list[SemanticGroupBy]) -> list[SemanticGroupBy]:
        keys = [(g.dimension, g.time_grain) for g in v]
        if len(set(keys)) != len(keys):
            raise ValueError("group_by (dimension, time_grain) pairs must be unique")
        return v


class ClarificationDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=500)
    ambiguity_type: Literal["metric", "dimension", "entity", "airport_or_location",
                            "carrier_role", "time_interpretation", "scope"]
    candidate_options: list[str] = Field(default_factory=list, max_length=10)


class UnsupportedDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=500)
    reason: Literal["no_approved_metric", "no_supported_dimension", "outside_retrieved_domains",
                    "insufficient_context", "unsupported_operation", "metrics_not_combinable"]
    suggested_supported_topics: list[str] = Field(default_factory=list, max_length=10)
    conflicting_metrics: list[str] = Field(default_factory=list, max_length=MAX_METRICS)


class SemanticRequestOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["semantic_request"]
    catalog_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    semantic_request: ConstrainedSemanticRequest
    clarification: None = None
    unsupported: None = None


class ClarificationOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["clarification_required"]
    catalog_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    semantic_request: None = None
    clarification: ClarificationDetails
    unsupported: None = None


class UnsupportedOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["unsupported"]
    catalog_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    semantic_request: None = None
    clarification: None = None
    unsupported: UnsupportedDetails


IntentParserResponse = Annotated[SemanticRequestOutcome | ClarificationOutcome | UnsupportedOutcome,
                                 Field(discriminator="outcome")]
