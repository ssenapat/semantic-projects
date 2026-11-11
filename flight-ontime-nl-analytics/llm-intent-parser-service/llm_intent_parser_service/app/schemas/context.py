"""LLM-safe semantic context models (CONTRACT section 3). Byte-identical copy of the Retriever's models."""
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
    combinable_with: str | None = None  # catalog group tag (C-3.2); None = cannot be combined



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
