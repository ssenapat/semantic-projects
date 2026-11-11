import os
import threading

import pytest
from fastapi.testclient import TestClient

from semantic_layer import SemanticLayer
from semantic_query_service import create_app
from semantic_query_service.settings import Settings
from svc_support import FakeExecutor

@pytest.fixture
def fake():
    return FakeExecutor()


@pytest.fixture
def make_client(fake):
    def make(settings=None, **kw):
        layer = SemanticLayer(executor=fake)
        app = create_app(layer=layer, settings=settings or Settings(**kw))
        return TestClient(app, raise_server_exceptions=False), layer
    return make


@pytest.fixture
def client(make_client):
    return make_client()[0]


@pytest.fixture
def db_client():
    if not (os.environ.get("PAGILA_PG_USER") and os.environ.get("PAGILA_PG_PASSWORD")):
        pytest.skip("PAGILA_PG_USER / PAGILA_PG_PASSWORD not set")
    return TestClient(create_app(settings=Settings()), raise_server_exceptions=False)
