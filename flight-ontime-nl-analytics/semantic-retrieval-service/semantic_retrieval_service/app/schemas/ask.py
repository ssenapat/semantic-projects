"""Retriever public API models (CONTRACT 4.1)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .retrieval import LlmSemanticContext, RetrievalSummary


class AuthorizationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_id: str | None = None
    allowed_domains: list[str] | None = None
    max_classification: str | None = None
    organization_scope: list[str] | None = None


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    conversation_context: list[str] = Field(default_factory=list, max_length=10)
    authorization_context: AuthorizationContext = Field(default_factory=AuthorizationContext)
    requested_limit: int = Field(default=500, ge=1, le=5000)


class AskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    catalog_version: str
    authorization_mode: str
    outcome: Literal["result", "clarification_required", "unsupported", "error"]  # "error" reserved
    clarification: dict | None = None
    unsupported: dict | None = None
    result: dict | None = None
    retrieval_summary: dict


class RetrieveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=4000)
    conversation_context: list[str] = Field(default_factory=list, max_length=10)
    authorization_context: AuthorizationContext = Field(default_factory=AuthorizationContext)


class RetrieveResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    catalog_version: str
    authorization_mode: str
    semantic_context: LlmSemanticContext
    retrieval_summary: RetrievalSummary
