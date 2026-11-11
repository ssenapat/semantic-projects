import os
import subprocess
import sys
from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SERVICE))
DEFS = str(SERVICE.parent / "semantic-layer" / "definitions")

from semantic_retrieval_service.app.services import catalog_loader  # noqa: E402
from semantic_retrieval_service.app.services.context_retriever import ContextRetriever  # noqa: E402


@pytest.fixture(scope="session")
def loaded():
    return catalog_loader.load(DEFS, "internal")


@pytest.fixture(scope="session")
def retriever(loaded):
    return ContextRetriever(loaded)


def run_script(name, *args, env=None):
    e = {**os.environ, "RETRIEVER_ARTIFACTS_DIR": str(env or SERVICE / "artifacts")}
    return subprocess.run([sys.executable, str(SERVICE / "scripts" / name), *args], capture_output=True, text=True, env=e)
