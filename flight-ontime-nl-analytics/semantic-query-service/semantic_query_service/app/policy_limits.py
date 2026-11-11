"""Reads the per-query metric limit from the semantic-layer query policy (CONTRACT C-3.3, C-4.1).

``max_metrics_per_query`` lives only in ``query_policy.yml``; nothing else binds it to a literal.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_KEY = "max_metrics_per_query"


def _definitions_dir(definitions_dir: str | Path | None) -> Path:
    if definitions_dir is not None:
        return Path(definitions_dir)
    env = os.environ.get("SEMANTIC_LAYER_DEFINITIONS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "semantic-layer" / "definitions"


def load_max_metrics_per_query(definitions_dir: str | Path | None = None) -> int:
    path = _definitions_dir(definitions_dir) / "policies" / "query_policy.yml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError(f"cannot read key '{_KEY}' from {path}: {exc}") from None
    value = data.get(_KEY) if isinstance(data, dict) else None
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RuntimeError(f"key '{_KEY}' in {path} must be an integer >= 1")
    return value


MAX_METRICS: int = load_max_metrics_per_query()
