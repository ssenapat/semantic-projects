from __future__ import annotations

from pathlib import Path

import pytest

SERVICE = Path(__file__).resolve().parents[2]
DEFS = (SERVICE.parent / "semantic-layer" / "definitions").resolve()


def fake_fetch(sql: str, params):
    if "ontime.airport" in sql:
        return [
            {"code": "ORD", "city_name": "Chicago, IL", "state_abr": "IL"},
            {"code": "MDW", "city_name": "Chicago, IL", "state_abr": "IL"},
            {"code": "JFK", "city_name": "New York, NY", "state_abr": "NY"},
            {"code": "LGA", "city_name": "New York, NY", "state_abr": "NY"},
            {"code": "ATL", "city_name": "Atlanta, GA", "state_abr": "GA"},
            {"code": "SEA", "city_name": "Seattle, WA", "state_abr": "WA"},
        ]
    if "ontime.carrier" in sql:
        return [{"unique_carrier": "AA"}, {"unique_carrier": "DL"}, {"unique_carrier": "UA"}]
    raise AssertionError(f"unexpected query: {sql}")


@pytest.fixture(scope="session")
def internal():
    from semantic_query_service.app.services import internal_catalog_loader
    return internal_catalog_loader.load(str(DEFS))


@pytest.fixture()
def registry(internal):
    from semantic_query_service.app.repositories.internal_semantic_registry import InternalSemanticRegistry
    return InternalSemanticRegistry(internal, "internal")


@pytest.fixture()
def mappings():
    from semantic_query_service.app.repositories.mapping_repository import MappingRepository
    m = MappingRepository(always_clarify=["Chicago"])
    m.load(fake_fetch)
    return m


@pytest.fixture()
def make_req():
    from semantic_query_service.app.schemas.semantic_request import QueryExecuteRequest

    def _mk(sr: dict, catalog_version: str = "sp1-x"):
        return QueryExecuteRequest(request_id="r1", catalog_version=catalog_version, semantic_request=sr)
    return _mk


@pytest.fixture()
def fetch():
    return fake_fetch
