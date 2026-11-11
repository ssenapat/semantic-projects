"""Reads/writes the generated index artifacts (catalog_manifest.json, semantic_documents.jsonl, vectors)."""
from __future__ import annotations

import json
from pathlib import Path

from ..schemas.catalog import SemanticDocument
from ..sp1_bridge import resolve_path

MANIFEST = "catalog_manifest.json"
DOCUMENTS = "semantic_documents.jsonl"
VECTORS_FAISS = "semantic_embeddings.faiss"
VECTORS_META = "semantic_vector_metadata.json"


def artifacts_dir(value: str) -> Path:
    return resolve_path(value)


def write_documents(directory: Path, documents: list[SemanticDocument]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / DOCUMENTS
    path.write_text("".join(d.model_dump_json() + "\n" for d in documents), encoding="utf-8")
    return path


def read_documents(directory: Path) -> list[SemanticDocument]:
    path = directory / DOCUMENTS
    return [SemanticDocument.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_manifest(directory: Path, manifest: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / MANIFEST
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_manifest(directory: Path) -> dict | None:
    path = directory / MANIFEST
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
