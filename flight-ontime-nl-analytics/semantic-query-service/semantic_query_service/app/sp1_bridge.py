"""Puts the read-only SP-1 package (``semantic_layer``) on ``sys.path``. SP-1 is imported, never edited."""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEFAULT_SRC = "../semantic-layer/src"  # relative to the service folder (CONTRACT §2 SEMANTIC_LAYER_SRC)
_SERVICE_DIR = Path(__file__).resolve().parents[2]


def resolve_src(src: str | None = None) -> Path:
    raw = src or os.environ.get("SEMANTIC_LAYER_SRC") or DEFAULT_SRC
    p = Path(raw)
    if not p.is_absolute():
        p = (_SERVICE_DIR / p).resolve()
    return p


def ensure_sp1_on_path(src: str | None = None) -> None:
    p = resolve_src(src)
    if not p.is_dir():
        raise FileNotFoundError(f"SP-1 source directory not found: {p}")
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)
