"""Settings (CONTRACT §2). Secrets are SecretStr, read only from the named env var, never logged."""
from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # env var names are the lower-cased field names (case-insensitive match)
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    semantic_layer_src: str | None = None
    semantic_layer_definitions_dir: str = "../semantic-layer/definitions"
    semantic_audience: str = "internal"
    log_level: str = "INFO"
    query_host: str = "127.0.0.1"
    query_port: int = 8082
    pghost: str = "localhost"
    pgport: int = 5432
    pgdatabase: str = "tran-stats"
    pguser: str = "postgres"
    pgpassword: SecretStr | None = None   # the ONLY password source (TRANSPORT_STATS_PG_PASSWORD is not read)
    query_reference_date: str | None = None
    query_statement_timeout_seconds: int = 30
    query_idle_tx_timeout_seconds: int = 60
    query_max_payload_bytes: int = 5_000_000
    query_explain_enabled: bool = False
    query_explain_max_cost: float = 1_000_000
    query_always_clarify_locations: str = "Chicago"
    query_audit_log_path: str = "./logs/query_audit.jsonl"


@lru_cache
def get_settings() -> Settings:
    return Settings()
