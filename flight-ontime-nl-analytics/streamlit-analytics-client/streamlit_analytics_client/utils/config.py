"""UI configuration from environment (CONTRACT §2). No secrets."""
from __future__ import annotations

import os

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
DEFAULT_TIMEOUT = 30.0


def base_url() -> str:
    return os.environ.get("UI_RETRIEVER_BASE_URL", DEFAULT_BASE_URL)


def timeout_seconds() -> float:
    try:
        v = float(os.environ.get("UI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT))
        return v if v > 0 else DEFAULT_TIMEOUT
    except ValueError:
        return DEFAULT_TIMEOUT


def csv_download_enabled() -> bool:
    return os.environ.get("UI_CSV_DOWNLOAD_ENABLED", "false").strip().lower() in ("1", "true", "yes")
