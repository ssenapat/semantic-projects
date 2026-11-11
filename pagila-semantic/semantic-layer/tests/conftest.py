import os
import pytest

from semantic_layer import SemanticLayer


@pytest.fixture(scope="session")
def layer():
    return SemanticLayer()


@pytest.fixture(scope="session")
def db_layer():
    if not (os.environ.get("PAGILA_PG_USER") and os.environ.get("PAGILA_PG_PASSWORD")):
        pytest.skip("PAGILA_PG_USER / PAGILA_PG_PASSWORD not set")
    return SemanticLayer()
