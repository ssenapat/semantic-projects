"""Shared helpers for the retriever scripts (env only; no secrets)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[1]
if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))


def settings() -> dict:
    return {
        "definitions_dir": os.environ.get("SEMANTIC_LAYER_DEFINITIONS_DIR", "../semantic-layer/definitions"),
        "audience": os.environ.get("SEMANTIC_AUDIENCE", "internal"),
        "artifacts_dir": os.environ.get("RETRIEVER_ARTIFACTS_DIR", "./artifacts"),
        "max_tokens": int(os.environ.get("RETRIEVER_MAX_CONTEXT_TOKENS", "12000")),
        "vector_enabled": os.environ.get("RETRIEVER_VECTOR_ENABLED", "false").strip().lower() in ("1", "true", "yes"),
        "provider": os.environ.get("RETRIEVER_EMBEDDING_PROVIDER", "none"),
    }
