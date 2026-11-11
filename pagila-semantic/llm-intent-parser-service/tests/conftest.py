import os

import pytest
from fastapi.testclient import TestClient

from intent_parser import IntentParser, create_app
from intent_parser.query_client import QueryServiceClient
from intent_parser.settings import Settings
from semantic_layer import SemanticLayer
from semantic_query_service import create_app as create_query_app
from semantic_query_service.settings import Settings as QuerySettings
from support import FakeExecutor, ScriptedLLM


@pytest.fixture
def executor():
    return FakeExecutor()


@pytest.fixture
def query_http(executor):
    """The real query service (real definitions, real compiler, real /v1/context policy) with a fake database."""
    layer = SemanticLayer(executor=executor)
    return TestClient(create_query_app(layer=layer, settings=QuerySettings()), raise_server_exceptions=False)


@pytest.fixture
def make_parser(query_http):
    def make(*turns, **settings):
        llm = ScriptedLLM(*turns)
        s = Settings(**settings)
        return IntentParser(llm, QueryServiceClient(client=query_http), s), llm
    return make


@pytest.fixture
def make_client(make_parser):
    def make(*turns, app_settings=None, **settings):
        parser, llm = make_parser(*turns, **settings)
        app = create_app(parser=parser, settings=app_settings or Settings(**settings))
        return TestClient(app, raise_server_exceptions=False), llm, parser
    return make


@pytest.fixture
def live_parser():
    s = Settings.from_env()
    if s.problem() or not (os.environ.get("PAGILA_PG_USER") and os.environ.get("PAGILA_PG_PASSWORD")):
        pytest.skip("needs INTENT_MODEL (plus AWS access, or INTENT_LLM_PROVIDER=anthropic with ANTHROPIC_API_KEY) and PAGILA_PG_*")
    from intent_parser.llm import build_llm
    http = TestClient(create_query_app(settings=QuerySettings()))
    return IntentParser(build_llm(s), QueryServiceClient(client=http), s)
