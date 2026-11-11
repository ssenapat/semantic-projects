"""LLM-safe context and retrieval summary models (CONTRACT §3, §4.1, §7.1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LlmSafeMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    description: str
    domain: str
    allowed_dimensions: list[str]
    time_dimension: str | None = None
    allowed_time_grains: list[str] = Field(default_factory=list)
    synonyms: list[str] = Field(default_factory=list)
    clarification_notes: list[str] = Field(default_factory=list)
    combinable_with: str | None = None   # catalog group tag (C-3.2). None = cannot be combined with any other metric.


class LlmSafeDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    description: str
    type: Literal["time", "categorical", "numeric", "boolean"]
    synonyms: list[str] = Field(default_factory=list)
    allowed_operators: list[str] = Field(default_factory=list)
    allowed_time_grains: list[str] = Field(default_factory=list)
    clarification_notes: list[str] = Field(default_factory=list)


class LlmSafeGlossaryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    term: str
    meaning: str
    maps_to: list[str] = Field(default_factory=list)
    ambiguity_action: Literal["none", "clarify", "reject"] = "none"
    clarification_question_template: str | None = None


class LlmSafeAmbiguityRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trigger: str
    alternatives: list[str]
    required_action: Literal["clarify", "reject"]
    question_template: str


class LlmSemanticContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    catalog_version: str
    selected_domains: list[str]
    metrics: list[LlmSafeMetric] = Field(max_length=15)
    dimensions: list[LlmSafeDimension] = Field(max_length=50)
    glossary: list[LlmSafeGlossaryEntry] = Field(max_length=30)
    ambiguity_rules: list[LlmSafeAmbiguityRule] = Field(max_length=20)
    response_rules: list[str]


class RetrievalSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_domains: list[str]
    metrics: list[str]
    dimensions: list[str]
    glossary_terms: list[str]
    ambiguity_triggers: list[str]
    match_sources: dict[str, list[Literal["exact", "synonym", "bm25", "vector"]]]
    retrieval_path: Literal["keyword", "keyword+vector"]
    context_tokens_estimate: int
    low_confidence: bool
    eligibility_audience: str


class MetricDimensionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str


class MetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    label: str
    description: str
    unit: str | None
    status: str
    time_basis: str | None
    allowed_dimensions: list[MetricDimensionRef]
    allowed_time_grains: list[str]
    synonyms: list[str]
    clarification_notes: list[str]
    caveats: list[str]
    combinable_with: str | None = None


class MetricCatalogResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    catalog_version: str
    metrics: list[MetricDefinition]
