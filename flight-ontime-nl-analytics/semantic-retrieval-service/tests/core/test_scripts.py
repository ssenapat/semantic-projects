import pytest

from conftest import run_script


def test_validate_catalog_ok():
    assert run_script("validate_catalog.py").returncode == 0


def test_validate_catalog_fails_on_bad_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("SEMANTIC_LAYER_DEFINITIONS_DIR", str(tmp_path / "missing"))
    assert run_script("validate_catalog.py").returncode == 1


def test_build_index_writes_artifacts(tmp_path):
    r = run_script("build_index.py", env=tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "catalog_manifest.json").is_file() and (tmp_path / "semantic_documents.jsonl").is_file()
    assert not (tmp_path / "semantic_embeddings.faiss").exists()


def test_evaluate_passes_and_fails():
    assert run_script("evaluate_retrieval.py", "--min-pass-rate", "0.9").returncode == 0
    assert run_script("evaluate_retrieval.py", "--min-pass-rate", "1.1").returncode == 1


def test_vector_retrieval_only_when_enabled(loaded):
    import os
    if os.environ.get("RETRIEVER_VECTOR_ENABLED", "false").lower() not in ("1", "true", "yes"):
        pytest.skip("vector flag off")
    from semantic_retrieval_service.app.services.context_retriever import ContextRetriever
    _, s = ContextRetriever(loaded, vector_enabled=True).retrieve("cancellations", [], "r")
    assert s.retrieval_path == "keyword+vector"
