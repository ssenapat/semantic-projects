"""Parser request/response models (CONTRACT 3 and 4.2); byte-compatible copies."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from ..policy_limits import MAX_METRICS

from .retrieval import LlmSemanticContext

RelativePeriod = Literal["today", "yesterday", "last_7_days", "last_30_days",
                         "last_complete_calendar_month", "last_3_complete_months",
                         "last_6_complete_months", "quarter_to_date", "year_to_date"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SemanticGroupBy(_M):
    dimension: str
    time_grain: Literal["day", "week", "month", "quarter", "year"] | None = None


class SemanticFilter(_M):
    dimension: str
    operator: Literal["equals", "not_equals", "in", "not_in", "between", "contains"]
    values: list[str] = Field(min_length=1, max_length=50)


class SemanticDateRange(_M):
    kind: Literal["absolute", "relative"]
    start_date: str | None = None
    end_date: str | None = None
    relative_period: RelativePeriod | None = None


class ConstrainedSemanticRequest(_M):
    metrics: list[str] = Field(min_length=1, max_length=MAX_METRICS)
    group_by: list[SemanticGroupBy] = Field(default_factory=list, max_length=8)
    filters: list[SemanticFilter] = Field(default_factory=list, max_length=20)
    date_range: SemanticDateRange | None = None
    requested_limit: int = Field(default=500, ge=1, le=5000)

    @field_validator("metrics")
    @classmethod
    def _unique_metrics(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("metrics must be unique")
        return v

    @field_validator("group_by")
    @classmethod
    def _unique_group_by(cls, v: list[SemanticGroupBy]) -> list[SemanticGroupBy]:
        pairs = [(g.dimension, g.time_grain) for g in v]
        if len(set(pairs)) != len(pairs):
            raise ValueError("group_by (dimension, time_grain) pairs must be unique")
        return v


class ClarificationDetails(_M):
    question: str = Field(min_length=1, max_length=500)
    ambiguity_type: Literal["metric", "dimension", "entity", "airport_or_location",
                            "carrier_role", "time_interpretation", "scope"]
    candidate_options: list[str] = Field(default_factory=list, max_length=10)


class UnsupportedDetails(_M):
    message: str = Field(min_length=1, max_length=500)
    reason: Literal["no_approved_metric", "no_supported_dimension", "outside_retrieved_domains",
                    "insufficient_context", "unsupported_operation", "metrics_not_combinable"]
    suggested_supported_topics: list[str] = Field(default_factory=list, max_length=10)
    conflicting_metrics: list[str] = Field(default_factory=list, max_length=MAX_METRICS)


class SemanticRequestOutcome(_M):
    outcome: Literal["semantic_request"]
    catalog_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    semantic_request: ConstrainedSemanticRequest
    clarification: None = None
    unsupported: None = None


class ClarificationOutcome(_M):
    outcome: Literal["clarification_required"]
    catalog_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    semantic_request: None = None
    clarification: ClarificationDetails
    unsupported: None = None


class UnsupportedOutcome(_M):
    outcome: Literal["unsupported"]
    catalog_version: str
    confidence: float = Field(ge=0.0, le=1.0)
    semantic_request: None = None
    clarification: None = None
    unsupported: UnsupportedDetails


IntentParserResponse = Annotated[SemanticRequestOutcome | ClarificationOutcome | UnsupportedOutcome,
                                 Field(discriminator="outcome")]
intent_parser_response_adapter: TypeAdapter = TypeAdapter(IntentParserResponse)


class ParserOptions(_M):
    temperature: float = Field(default=0.0, ge=0.0, le=0.2)
    max_output_tokens: int = Field(default=1000, ge=200, le=2000)
    allow_clarification: bool = True
    allow_unsupported: bool = True


class IntentParserRequest(_M):
    request_id: str
    question: str = Field(min_length=1, max_length=4000)
    conversation_context: list[str] = Field(default_factory=list, max_length=10)
    semantic_context: LlmSemanticContext
    parser_options: ParserOptions = Field(default_factory=ParserOptions)
