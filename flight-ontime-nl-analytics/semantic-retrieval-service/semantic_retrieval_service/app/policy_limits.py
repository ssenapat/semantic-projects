"""Reads the per-query metric limit from the semantic-layer query policy (CONTRACT C-3.3).

The value lives only in ``semantic-layer/definitions/policies/query_policy.yml``.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_KEY = "max_metrics_per_query"


def load_max_metrics_per_query(definitions_dir: str | Path | None = None) -> int:
    if definitions_dir is None:
        env = os.environ.get("SEMANTIC_LAYER_DEFINITIONS_DIR")
        definitions_dir = env if env else Path(__file__).resolve().parents[3] / "semantic-layer" / "definitions"
    path = Path(definitions_dir) / "policies" / "query_policy.yml"
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh)
    except OSError as exc:
        raise RuntimeError(f"cannot read query policy {path} (key {_KEY}): {exc}") from exc
    value = data.get(_KEY) if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(f"{path}: key '{_KEY}' is missing or not an integer >= 1")
    return value


MAX_METRICS: int = load_max_metrics_per_query()
