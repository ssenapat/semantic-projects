"""Settings read from environment variables only (CONTRACT section 2). Secrets are SecretStr, never logged."""
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SERVICE_NAME = "llm-intent-parser-service"
SERVICE_VERSION = "0.2.0"
DEFAULT_STUB_FILE = Path(__file__).parent / "services" / "stub_responses.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False, populate_by_name=True)

    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    parser_host: str = Field(default="127.0.0.1", validation_alias="PARSER_HOST")
    parser_port: int = Field(default=8081, validation_alias="PARSER_PORT")
    parser_llm_client: Literal["bedrock", "anthropic", "stub"] = Field(default="bedrock", validation_alias="PARSER_LLM_CLIENT")
    # AWS Bedrock (default): credentials come from the standard AWS chain (env vars, AWS_PROFILE, SSO, instance role); no API key.
    aws_region: str | None = Field(default=None, validation_alias=AliasChoices("AWS_REGION", "AWS_DEFAULT_REGION"))
    bedrock_model_id: str | None = Field(default=None, validation_alias="BEDROCK_MODEL_ID")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5-5", validation_alias="ANTHROPIC_MODEL")
    parser_llm_timeout_seconds: float = Field(default=20.0, validation_alias="PARSER_LLM_TIMEOUT_SECONDS")
    parser_max_repair_attempts: int = Field(default=1, ge=0, le=3, validation_alias="PARSER_MAX_REPAIR_ATTEMPTS")
    parser_temperature: float = Field(default=0.0, validation_alias="PARSER_TEMPERATURE")
    parser_max_output_tokens: int = Field(default=1000, validation_alias="PARSER_MAX_OUTPUT_TOKENS")
    parser_stub_responses_file: str | None = Field(default=None, validation_alias="PARSER_STUB_RESPONSES_FILE")
    parser_rate_limit_per_minute: int = Field(default=60, ge=1, validation_alias="PARSER_RATE_LIMIT_PER_MINUTE")

    @field_validator("parser_temperature")
    @classmethod
    def _clamp_temperature(cls, v: float) -> float:
        return min(0.2, max(0.0, v))

    @field_validator("anthropic_api_key", mode="before")
    @classmethod
    def _blank_key_is_unset(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @property
    def stub_file(self) -> Path:
        return Path(self.parser_stub_responses_file) if self.parser_stub_responses_file else DEFAULT_STUB_FILE

    def is_configured(self) -> bool:
        if self.parser_llm_client == "stub":
            return True
        if self.parser_llm_client == "bedrock":
            return bool((self.aws_region or "").strip() and (self.bedrock_model_id or "").strip())
        key = self.anthropic_api_key.get_secret_value() if self.anthropic_api_key else ""
        return bool(key.strip() and self.anthropic_model.strip())


def not_ready_detail(settings: Settings) -> str:
    """Names the missing variable, never a value (A8 / I8)."""
    missing = []
    if settings.parser_llm_client == "bedrock":
        if not (settings.aws_region or "").strip():
            missing.append("AWS_REGION")
        if not (settings.bedrock_model_id or "").strip():
            missing.append("BEDROCK_MODEL_ID")
        if not missing:
            return "The LLM client is not configured."
        return f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set (PARSER_LLM_CLIENT=bedrock)"
    if not (settings.anthropic_api_key and settings.anthropic_api_key.get_secret_value().strip()):
        missing.append("ANTHROPIC_API_KEY")
    if not settings.anthropic_model.strip():
        missing.append("ANTHROPIC_MODEL")
    if not missing:
        return "The LLM client is not configured."
    return f"{' and '.join(missing)} {'is' if len(missing) == 1 else 'are'} not set (PARSER_LLM_CLIENT=anthropic)"


def get_settings() -> Settings:
    return Settings()
