"""LLM clients: the LlmClient protocol, the Anthropic client and the deterministic stub (REQ-041..043)."""
import inspect
import json
import logging
import re
from pathlib import Path
from typing import Protocol

from ..config import Settings
from ..observability.logging import log_event
from .prompt_builder import unescape_text
from .text import normalize


class LlmProviderError(Exception):
    """Provider timeout, connection or API failure (maps to 502)."""


class LlmRateLimited(Exception):
    """Provider rate limit (maps to 429)."""


class LlmClient(Protocol):
    def complete_json(self, *, system: str, user: str, json_schema: dict, temperature: float,
                      max_output_tokens: int, timeout_s: float) -> str:
        """raises LlmProviderError, LlmRateLimited"""


class AnthropicLlmClient:
    """Uses ANTHROPIC_API_KEY / ANTHROPIC_MODEL from the environment settings only. The SDK is imported lazily."""

    _extra_body: dict | None = None  # subclass hook: extra request-body fields

    def __init__(self, api_key: str, model: str, sdk_client=None):
        self._model = model
        if sdk_client is None:
            import anthropic  # lazy: tests and stub mode never need it
            sdk_client = anthropic.Anthropic(api_key=api_key, max_retries=0)
        self._sdk = sdk_client
        self._accepted = self._accepted_kwargs(sdk_client)
        if self._accepted is not None and "temperature" not in self._accepted:
            log_event(event="llm_temperature_ignored", request_id="", level=logging.WARNING)  # once, at startup

    @staticmethod
    def _accepted_kwargs(sdk_client) -> set[str] | None:
        """Parameter names of messages.create; None when it takes **kwargs or cannot be inspected."""
        try:
            params = inspect.signature(sdk_client.messages.create).parameters
        except (TypeError, ValueError, AttributeError):
            return None
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()):
            return None
        return set(params)

    def complete_json(self, *, system: str, user: str, json_schema: dict, temperature: float,
                      max_output_tokens: int, timeout_s: float) -> str:
        full_system = (f"{system}\n\nJSON schema for the single object you must return:\n"
                       f"{json.dumps(json_schema, separators=(',', ':'))}")
        kwargs = {"model": self._model, "max_tokens": max_output_tokens, "temperature": temperature,
                  "system": full_system, "messages": [{"role": "user", "content": user}], "timeout": timeout_s}
        if self._extra_body:
            kwargs["extra_body"] = dict(self._extra_body)
        if self._accepted is not None:
            kwargs = {k: v for k, v in kwargs.items() if k in self._accepted}
        try:
            msg = self._sdk.messages.create(**kwargs)
        except TypeError:
            log_event(event="llm_call", request_id="", level=logging.ERROR, error_code="llm_client_misconfigured")
            raise LlmProviderError("llm_client_misconfigured") from None
        except Exception as exc:  # noqa: BLE001 - mapped to safe domain errors, text never propagated
            if type(exc).__name__ == "RateLimitError" or getattr(exc, "status_code", None) == 429:
                raise LlmRateLimited("provider rate limit") from None
            raise LlmProviderError(type(exc).__name__) from None
        parts = [getattr(b, "text", "") for b in getattr(msg, "content", []) if getattr(b, "type", "text") == "text"]
        return "".join(parts)


TOPICS_PLACEHOLDER = "{{metric_topics}}"


def _topics_from_context(ctx: dict) -> list[str]:
    """At most 3 topics, taken from the context metric labels; [] when the context has no metrics (A7)."""
    labels = [m.get("label", "") for m in ctx.get("metrics", []) if m.get("label")]
    return labels[:3]


class BedrockLlmClient(AnthropicLlmClient):
    """Claude on AWS Bedrock through the Anthropic SDK's Bedrock client. No API key: AWS credentials come from the
    standard chain (env vars, AWS_PROFILE, SSO, instance role). `model` is the Bedrock model or inference-profile id."""

    # The SDK's default anthropic_version is rejected by Bedrock (400 "Invalid API version"); pin the Bedrock one.
    _extra_body = {"anthropic_version": "bedrock-2023-05-31"}

    def __init__(self, region: str, model: str, sdk_client=None):
        if sdk_client is None:
            from anthropic import AnthropicBedrock  # lazy; needs the anthropic[bedrock] extra (boto3)
            sdk_client = AnthropicBedrock(aws_region=region, max_retries=0)
        super().__init__("", model, sdk_client=sdk_client)


class StubLlmClient:
    """Maps normalize(question) to a fixture outcome; {{catalog_version}} is substituted. Offline and deterministic."""

    def __init__(self, fixtures_file: Path | str):
        data = json.loads(Path(fixtures_file).read_text(encoding="utf-8"))
        self._by_question: dict[str, dict] = {}
        for fx in data["fixtures"]:
            for q in fx["questions"]:
                self._by_question[self._key(q)] = fx["outcome"]

    @staticmethod
    def _key(question: str) -> str:
        """Case-insensitive, punctuation-insensitive, optional leading 'show' ignored (A1)."""
        n = normalize(question)
        return n[len("show "):] if n.startswith("show ") else n

    @staticmethod
    def _sections(user: str) -> tuple[str, dict]:
        q_start = user.rfind("<question>\n")
        q_end = user.rfind("\n</question>")
        question = user[q_start + len("<question>\n"):q_end] if q_start >= 0 and q_end > q_start else ""
        m = re.search(r"<semantic_context>\n(.*?)\n</semantic_context>", user, re.S)
        ctx: dict = {}
        if m:
            try:
                ctx = json.loads(m.group(1))
            except ValueError:
                pass
        return unescape_text(question), ctx

    def complete_json(self, *, system: str, user: str, json_schema: dict, temperature: float,
                      max_output_tokens: int, timeout_s: float) -> str:
        question, ctx = self._sections(user)
        version = str(ctx.get("catalog_version", ""))
        outcome = self._by_question.get(self._key(question))
        if outcome is not None and outcome.get("outcome") == "unsupported":
            outcome = json.loads(json.dumps(outcome))
            topics = outcome["unsupported"].get("suggested_supported_topics", [])
            if topics == [TOPICS_PLACEHOLDER]:  # fixtures may ask for context-derived topics (A7)
                outcome["unsupported"]["suggested_supported_topics"] = _topics_from_context(ctx)
        if outcome is None:
            outcome = {"outcome": "unsupported", "catalog_version": "{{catalog_version}}", "confidence": 0.3,
                       "unsupported": {"message": "The approved context does not cover this question.",
                                       "reason": "insufficient_context",
                                       "suggested_supported_topics": _topics_from_context(ctx)}}
        return json.dumps(outcome).replace("{{catalog_version}}", version)


def build_llm_client(settings: Settings) -> LlmClient | None:
    """Returns None when the client is unconfigured (readiness then reports 503 parser_not_ready)."""
    if settings.parser_llm_client == "stub":
        return StubLlmClient(settings.stub_file)
    if not settings.is_configured():
        return None
    if settings.parser_llm_client == "bedrock":
        return BedrockLlmClient(settings.aws_region.strip(), settings.bedrock_model_id.strip())
    return AnthropicLlmClient(settings.anthropic_api_key.get_secret_value(), settings.anthropic_model)
