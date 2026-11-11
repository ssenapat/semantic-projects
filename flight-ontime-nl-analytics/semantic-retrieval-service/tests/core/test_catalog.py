import re

import pytest

from conftest import DEFS
from semantic_retrieval_service.app.services import catalog_loader, catalog_validator


def test_versions_follow_contract(loaded):
    v = loaded.versions
    assert v.catalog_version == "sp1-" + loaded.catalog.definitions_version
    assert v.source_checksum == loaded.catalog.definitions_version
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", v.build_timestamp)
    assert all(d.catalog_version == v.catalog_version for d in loaded.documents)


def test_sp1_is_imported_not_copied(loaded):
    import semantic_layer
    assert "semantic-layer" in semantic_layer.__file__


def test_missing_dir_raises():
    with pytest.raises(catalog_loader.CatalogLoadError):
        catalog_loader.load("/nonexistent/definitions", "internal")


def test_unknown_audience_fails(loaded):
    assert catalog_validator.validate(loaded.catalog, "nobody")


def test_only_eligible_documents(loaded):
    names = {(d.document_type, d.canonical_name) for d in loaded.documents}
    assert ("metric", "diversion_rate") not in names      # draft
    assert ("dimension", "cancellation_reason") not in names  # draft
    assert ("metric", "cancellation_rate") in names
    consumer = catalog_loader.load(DEFS, "consumer")      # approved only: nothing validated is exposed
    assert not [d for d in consumer.documents if d.document_type in ("metric", "dimension")]
