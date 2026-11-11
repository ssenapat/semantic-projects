"""Constrained request models (CONTRACT §3, byte-identical copy of the shared models used by Query)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..policy_limits import MAX_METRICS

RelativePeriod = Literal["today", "yesterday", "last_7_days", "last_30_days",
    "last_complete_calendar_month", "last_3_complete_months", "last_6_complete_months",
    "quarter_to_date", "year_to_date"]


class AuthorizationContext(BaseModel):          # Retriever, Query
    model_config = ConfigDict(extra="forbid")
    subject_id: str | None = None
    allowed_domains: list[str] | None = None
    max_classification: str | None = None
    organization_scope: list[str] | None = None


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
    start_date: str | None = None          # YYYY-MM-DD, inclusive (absolute)
    end_date: str | None = None            # YYYY-MM-DD, inclusive (absolute)
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
    def _unique_metrics(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("metrics must be unique")
        return v

    @field_validator("group_by")
    @classmethod
    def _unique_group_by(cls, v: list[SemanticGroupBy]) -> list[SemanticGroupBy]:
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


class QueryExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    catalog_version: str
    semantic_request: ConstrainedSemanticRequest
    authorization_context: AuthorizationContext = Field(default_factory=AuthorizationContext)


class QueryClarificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["clarification_required"]
    request_id: str
    clarification: ClarificationDetails


class QueryUnsupportedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["unsupported"]
    request_id: str
    unsupported: UnsupportedDetails
