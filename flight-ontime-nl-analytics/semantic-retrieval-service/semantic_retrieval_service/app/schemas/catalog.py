"""Catalog document model (CONTRACT §7.1, REQ-014)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SemanticDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str
    document_type: Literal["metric", "dimension", "glossary", "ambiguity_rule", "unavailable_concept"]
    canonical_name: str
    label: str
    description: str
    domain: str
    semantic_model: str
    status: str
    semantic_version: str
    catalog_version: str
    classification: str
    synonyms: list[str]
    tags: list[str]
    searchable_text: str
    llm_safe_payload: dict
    internal_payload: dict
    source_file: str
    source_checksum: str
