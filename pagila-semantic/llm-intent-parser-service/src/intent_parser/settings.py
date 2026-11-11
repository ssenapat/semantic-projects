from __future__ import annotations

import os
from dataclasses import dataclass, field


def _flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def parse_keys(raw: str) -> dict[str, str]:
    """'name:key,name2:key2' -> {key: name}; keys need at least 16 characters."""
    out: dict[str, str] = {}
    for item in filter(None, (x.strip() for x in raw.split(","))):
        name, sep, key = item.partition(":")
        if not sep or not name or len(key) < 16:
            raise ValueError("INTENT_API_KEYS entries must look like name:key with a key of at least 16 characters")
        out[key] = name
    return out


@dataclass
class Settings:
    query_service_url: str = "http://127.0.0.1:8000"
    query_service_key: str | None = None            # forwarded as X-API-Key
    llm_provider: str = "bedrock"                   # "bedrock" or "anthropic"
    model: str = ""                                 # Bedrock model / inference profile id; required for bedrock
    aws_region: str | None = None                   # default: AWS_REGION / the profile's region
    aws_profile: str | None = None                  # default: AWS_PROFILE / the default profile
    max_question_chars: int = 1000
    max_llm_calls: int = 3                          # first attempt plus up to two repairs
    max_tokens: int = 1024
    llm_timeout_seconds: float = 30.0
    max_concurrent: int = 4
    queue_wait_seconds: float = 2.0
    max_body_bytes: int = 32 * 1024
    log_questions: bool = False                     # questions may contain personal data
    api_keys: dict[str, str] = field(default_factory=dict)

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_keys)

    def problem(self) -> str | None:
        """Why the service cannot start with these settings, or None."""
        if self.llm_provider not in {"bedrock", "anthropic"}:
            return "INTENT_LLM_PROVIDER must be bedrock or anthropic"
        if not self.model:
            return ("INTENT_MODEL is not set (use a Bedrock model or inference profile id)"
                    if self.llm_provider == "bedrock" else "INTENT_MODEL is not set")
        return None

    @classmethod
    def from_env(cls) -> "Settings":
        e = os.environ.get
        return cls(
            query_service_url=e("SEMANTIC_QUERY_URL", cls.query_service_url).rstrip("/"),
            query_service_key=e("SEMANTIC_QUERY_API_KEY") or None,
            llm_provider=e("INTENT_LLM_PROVIDER", cls.llm_provider).strip().lower(),
            model=e("INTENT_MODEL", cls.model).strip(),
            aws_region=e("AWS_REGION") or e("AWS_DEFAULT_REGION") or None,
            aws_profile=e("AWS_PROFILE") or None,
            max_question_chars=int(e("INTENT_MAX_QUESTION_CHARS", cls.max_question_chars)),
            max_llm_calls=int(e("INTENT_MAX_LLM_CALLS", cls.max_llm_calls)),
            max_tokens=int(e("INTENT_MAX_TOKENS", cls.max_tokens)),
            llm_timeout_seconds=float(e("INTENT_LLM_TIMEOUT_SECONDS", cls.llm_timeout_seconds)),
            max_concurrent=int(e("INTENT_MAX_CONCURRENT", cls.max_concurrent)),
            log_questions=_flag("INTENT_LOG_QUESTIONS"),
            api_keys=parse_keys(e("INTENT_API_KEYS", "")),
        )
