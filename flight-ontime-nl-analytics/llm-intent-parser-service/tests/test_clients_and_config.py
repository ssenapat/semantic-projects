import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from llm_intent_parser_service.app.config import Settings
from llm_intent_parser_service.app.services.llm_client import (
    AnthropicLlmClient, LlmProviderError, LlmRateLimited, StubLlmClient, build_llm_client)
from llm_intent_parser_service.app.services.prompt_builder import ESCAPED_TEXT_NOTE, SYSTEM_PROMPT

PKG = Path(__file__).resolve().parents[1] / "llm_intent_parser_service"
KW = dict(system="s", user="u", json_schema={"type": "object"}, temperature=0.0, max_output_tokens=300, timeout_s=5)


def test_client_selection_by_env(monkeypatch):
    monkeypatch.setenv("PARSER_LLM_CLIENT", "stub")
    assert isinstance(build_llm_client(Settings()), StubLlmClient)
    monkeypatch.setenv("PARSER_LLM_CLIENT", "anthropic")
    assert build_llm_client(Settings()) is None  # no key -> unconfigured, no network, no SDK needed


def test_defaults_and_secret_handling(monkeypatch):
    s = Settings()
    assert (s.parser_llm_client, s.anthropic_model, s.parser_port, s.parser_max_repair_attempts) == (
        "bedrock", "claude-sonnet-5-5", 8081, 1)
    monkeypatch.setenv("PARSER_LLM_CLIENT", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-123")
    monkeypatch.setenv("PARSER_TEMPERATURE", "0.9")
    s = Settings()
    assert "sk-secret-123" not in repr(s) and "sk-secret-123" not in str(s.model_dump())
    assert s.parser_temperature == 0.2 and s.is_configured()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert not Settings().is_configured()


def test_anthropic_client_uses_env_model_and_maps_errors():
    calls = []

    class Msgs:
        def create(self, **kw):
            calls.append(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text='{"a":1}')])

    c = AnthropicLlmClient("k", "claude-test", sdk_client=SimpleNamespace(messages=Msgs()))
    assert c.complete_json(**KW) == '{"a":1}'
    assert calls[0]["model"] == "claude-test" and calls[0]["max_tokens"] == 300 and calls[0]["timeout"] == 5
    assert '"type":"object"' in calls[0]["system"]

    class RateLimitError(Exception):
        pass

    for exc, want in ((RateLimitError("x"), LlmRateLimited), (TimeoutError("t"), LlmProviderError)):
        class Boom:
            def create(self, **kw):
                raise exc
        with pytest.raises(want):
            AnthropicLlmClient("k", "m", sdk_client=SimpleNamespace(messages=Boom())).complete_json(**KW)


def test_stub_fixtures_file_substitution_and_override(tmp_path):
    stub = StubLlmClient(PKG / "app" / "services" / "stub_responses.json")
    user = '<semantic_context>\n{"catalog_version":"sp1-zz"}\n</semantic_context>\n<question>\nShow flight delays\n</question>'
    assert json.loads(stub.complete_json(**{**KW, "user": user}))["catalog_version"] == "sp1-zz"
    f = tmp_path / "f.json"
    f.write_text(json.dumps({"fixtures": []}))
    out = json.loads(StubLlmClient(f).complete_json(**{**KW, "user": user}))
    assert out["unsupported"]["reason"] == "insufficient_context"


def test_system_prompt_is_spec_text():
    # the repo's own versioned specs/ (repo root = parents[2]), not a copy outside the repo
    spec = Path(__file__).resolve().parents[2] / "specs" / "three_service_natural_language_analytics_architecture_spec.md"
    if not spec.exists():
        pytest.skip("spec not present")
    text = spec.read_text(encoding="utf-8")
    block = text.split("## 17. Required System Prompt")[1].split("```text\n")[1].split("\n```")[0]
    norm = lambda t: " ".join(t.split())
    assert ESCAPED_TEXT_NOTE in SYSTEM_PROMPT  # A6: the only addition to the spec text
    assert norm(block) == norm(SYSTEM_PROMPT.replace(ESCAPED_TEXT_NOTE, ""))


FORBIDDEN_IMPORTS = {"semantic_layer", "psycopg", "psycopg2", "sqlalchemy", "faiss", "httpx", "requests", "subprocess"}
FORBIDDEN_FILES = {"redshift_client.py", "sql_compiler.py", "sql_executor.py", "semantic_catalog_loader.py",
                   "vector_retriever.py", "faiss_index.py", "web_search.py", "browser_tool.py", "shell_executor.py"}


def test_no_forbidden_imports_or_files():
    for path in PKG.rglob("*.py"):
        assert path.name not in FORBIDDEN_FILES
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module]
            for m in mods:
                assert m.split(".")[0] not in FORBIDDEN_IMPORTS, f"{path}: {m}"
    assert not any(p.name in FORBIDDEN_FILES for p in PKG.rglob("*"))


def test_no_secrets_in_package_files():
    for p in list(PKG.rglob("*.py")) + list(PKG.rglob("*.json")):
        assert "sk-ant" not in p.read_text(encoding="utf-8")


def test_bedrock_needs_region_and_model_but_no_api_key(monkeypatch):
    from types import SimpleNamespace
    from llm_intent_parser_service.app.config import not_ready_detail
    from llm_intent_parser_service.app.services.llm_client import BedrockLlmClient
    for v in ("AWS_REGION", "AWS_DEFAULT_REGION", "BEDROCK_MODEL_ID", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("PARSER_LLM_CLIENT", "bedrock")
    assert not Settings().is_configured() and build_llm_client(Settings()) is None
    assert "AWS_REGION and BEDROCK_MODEL_ID are not set" in not_ready_detail(Settings())
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("BEDROCK_MODEL_ID", "some.model-id")
    assert Settings().is_configured()
    seen = {}

    class Msgs:
        def create(self, **kw):
            seen.update(kw)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="{}")])

    c = BedrockLlmClient("us-east-1", "some.model-id", sdk_client=SimpleNamespace(messages=Msgs()))
    assert c.complete_json(system="s", user="u", json_schema={}, temperature=0.0, max_output_tokens=10, timeout_s=5) == "{}"
    assert seen["model"] == "some.model-id"


def test_service_version_is_0_2_0():
    import tomllib
    from llm_intent_parser_service.app.config import SERVICE_VERSION
    meta = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    assert SERVICE_VERSION == "0.2.0" and meta["project"]["version"] == "0.2.0"
    assert "pyyaml==6.0.3" in meta["project"]["dependencies"]
