"""Reads the per-query metric limit from the semantic-layer query policy (CONTRACT C-3.3).

The parser must not import `semantic_layer`; it reads the policy YAML directly.
"""
import os
from pathlib import Path

import yaml

POLICY_KEY = "max_metrics_per_query"


def _policy_path(definitions_dir: str | Path | None) -> Path:
    if definitions_dir is None:
        env = os.environ.get("SEMANTIC_LAYER_DEFINITIONS_DIR", "").strip()
        definitions_dir = env or Path(__file__).resolve().parents[3] / "semantic-layer" / "definitions"
    return Path(definitions_dir) / "policies" / "query_policy.yml"


def load_max_metrics_per_query(definitions_dir: str | Path | None = None) -> int:
    path = _policy_path(definitions_dir)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"cannot read query policy {path} (key {POLICY_KEY}): {exc.__class__.__name__}") from exc
    value = data.get(POLICY_KEY) if isinstance(data, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RuntimeError(f"{POLICY_KEY} in {path} must be an integer >= 1")
    return value


MAX_METRICS: int = load_max_metrics_per_query()
