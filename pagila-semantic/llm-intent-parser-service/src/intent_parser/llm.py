"""The one place that talks to Claude. Everything else uses the LLMClient protocol, so tests can script it."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class LLMError(Exception):
    """The model could not be reached or answered unusably. Never carries provider details."""


@dataclass
class LLMTurn:
    tool_name: str | None
    tool_input: dict
    tool_use_id: str | None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0

    def assistant_message(self) -> dict:
        return {"role": "assistant", "content": [
            {"type": "tool_use", "id": self.tool_use_id, "name": self.tool_name, "input": self.tool_input}]}


class LLMClient(Protocol):
    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMTurn: ...


class _MessagesClient:
    """Shared Messages API call. Subclasses only decide which SDK client to build."""
    _client: Any
    _errors: tuple[type[Exception], ...]

    _force_tool = True      # ask for a tool call; some models refuse tool_choice "any" and get "auto" instead

    def _create(self, system: str, messages: list[dict], tools: list[dict]):
        choice = ({"type": "any", "disable_parallel_tool_use": True} if self._force_tool
                  else {"type": "auto", "disable_parallel_tool_use": True})
        return self._client.messages.create(
            model=self.model, max_tokens=self.max_tokens,
            # The catalog and rules are identical for every question, so they are cached.
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=tools, tool_choice=choice, messages=messages)

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> LLMTurn:
        try:
            try:
                resp = self._create(system, messages, tools)
            except self._errors as exc:
                if not (self._force_tool and "tool_choice" in str(exc)):
                    raise
                self._force_tool = False        # remembered, so only the first call pays for the retry
                resp = self._create(system, messages, tools)
        except self._errors as exc:
            raise LLMError(type(exc).__name__) from None
        use = next((b for b in resp.content if b.type == "tool_use"), None)
        u = resp.usage
        return LLMTurn(tool_name=use.name if use else None, tool_input=dict(use.input) if use else {},
                       tool_use_id=use.id if use else None,
                       input_tokens=u.input_tokens, output_tokens=u.output_tokens,
                       cache_read_tokens=getattr(u, "cache_read_input_tokens", 0) or 0)


class AnthropicClient(_MessagesClient):
    """Anthropic API directly. Needs ANTHROPIC_API_KEY."""
    def __init__(self, model: str, max_tokens: int, timeout: float, api_key: str | None = None):
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self._errors = (anthropic.APIError,)
        self.model, self.max_tokens = model, max_tokens


class BedrockClient(_MessagesClient):
    """Claude on AWS Bedrock. Credentials come from the normal AWS chain (SSO profile, env vars, role),
    so no API key is handled here. `model` is a Bedrock model id or inference profile id."""
    def __init__(self, model: str, max_tokens: int, timeout: float,
                 region: str | None = None, profile: str | None = None):
        import anthropic
        from botocore.exceptions import BotoCoreError, ClientError
        self._client = anthropic.AnthropicBedrock(aws_region=region, aws_profile=profile,
                                                  timeout=timeout, max_retries=2)
        # BotoCoreError covers expired SSO tokens and missing credentials.
        self._errors = (anthropic.APIError, BotoCoreError, ClientError)
        self.model, self.max_tokens = model, max_tokens


def build_llm(settings) -> LLMClient:
    if settings.llm_provider == "anthropic":
        return AnthropicClient(settings.model, settings.max_tokens, settings.llm_timeout_seconds)
    return BedrockClient(settings.model, settings.max_tokens, settings.llm_timeout_seconds,
                         settings.aws_region, settings.aws_profile)
