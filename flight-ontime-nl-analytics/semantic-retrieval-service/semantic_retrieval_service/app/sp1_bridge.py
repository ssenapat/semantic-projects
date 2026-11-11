"""Puts the SP-1 semantic layer source on sys.path so it is imported, never copied (REQ-003)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

SERVICE_DIR = Path(__file__).resolve().parents[2]
DEFAULT_SRC = "../semantic-layer/src"
DEFAULT_DEFINITIONS = "../semantic-layer/definitions"


def resolve_path(value: str) -> Path:
    """Resolve a (possibly relative) path against the service folder."""
    p = Path(value).expanduser()
    return p if p.is_absolute() else (SERVICE_DIR / p).resolve()


def default_src() -> str:
    return os.environ.get("SEMANTIC_LAYER_SRC", DEFAULT_SRC)


def default_definitions_dir() -> str:
    return os.environ.get("SEMANTIC_LAYER_DEFINITIONS_DIR", DEFAULT_DEFINITIONS)


def ensure_sp1_on_path(src: str) -> None:
    path = resolve_path(src)
    if not (path / "semantic_layer").is_dir():
        raise FileNotFoundError("SP-1 semantic_layer package not found at the configured SEMANTIC_LAYER_SRC")
    text = str(path)
    if text not in sys.path:
        sys.path.insert(0, text)
