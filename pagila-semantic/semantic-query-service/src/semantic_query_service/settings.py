"""Service settings, read from the environment only."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def _parse_keys(raw: str) -> dict[str, str]:
    """'name:key,name2:key2' -> {key: name}. Keys must be at least 16 characters."""
    out: dict[str, str] = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        name, sep, key = item.partition(":")
        if not sep or not name or len(key) < 16:
            raise ValueError("SEMANTIC_API_KEYS entries must look like name:key with a key of at least 16 characters")
        out[key] = name
    return out


@dataclass
class Settings:
    api_keys: dict[str, str] = field(default_factory=dict)   # key -> caller name
    allow_draft: bool = False
    max_body_bytes: int = 64 * 1024
    max_concurrent_queries: int = 8
    queue_wait_seconds: float = 2.0
    cors_origins: list[str] = field(default_factory=list)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            api_keys=_parse_keys(os.environ.get("SEMANTIC_API_KEYS", "")),
            allow_draft=_flag("SEMANTIC_ALLOW_DRAFT"),
            max_body_bytes=int(os.environ.get("SEMANTIC_MAX_BODY_BYTES", 64 * 1024)),
            max_concurrent_queries=int(os.environ.get("SEMANTIC_MAX_CONCURRENT_QUERIES", 8)),
            queue_wait_seconds=float(os.environ.get("SEMANTIC_QUEUE_WAIT_SECONDS", 2.0)),
            cors_origins=[o.strip() for o in os.environ.get("SEMANTIC_CORS_ORIGINS", "").split(",") if o.strip()],
        )
