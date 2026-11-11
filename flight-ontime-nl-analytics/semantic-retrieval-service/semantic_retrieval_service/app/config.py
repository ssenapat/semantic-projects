"""Settings read from the env var names in CONTRACT section 2 only."""
from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    semantic_layer_src: str = "../semantic-layer/src"
    semantic_layer_definitions_dir: str = "../semantic-layer/definitions"
    semantic_audience: str = "internal"
    log_level: str = "INFO"
    retriever_host: str = "127.0.0.1"
    retriever_port: int = 8080
    parser_base_url: str = "http://127.0.0.1:8081"
    query_service_base_url: str = "http://127.0.0.1:8082"
    retriever_parser_timeout_seconds: float = 25
    retriever_query_timeout_seconds: float = 40
    retriever_rate_limit_per_minute: int = 60
    retriever_max_context_tokens: int = 12000
    retriever_vector_enabled: bool = False
    retriever_embedding_provider: str = "none"
    retriever_embedding_api_key: SecretStr | None = None
    retriever_artifacts_dir: str = "./artifacts"
