"""Loads the SP-1 catalog for the Query Service (REQ-006). Same version code as the Retriever (CONTRACT §6.1)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from ..sp1_bridge import ensure_sp1_on_path


@dataclass(frozen=True)
class CatalogVersions:
    catalog_version: str      # "sp1-" + catalog.definitions_version
    semantic_version: str     # semantic_layer.__version__
    source_checksum: str      # catalog.definitions_version (sha256[:12] over definitions/*.yml)
    build_timestamp: str      # UTC load time


@dataclass
class InternalCatalog:
    catalog: "semantic_layer.models.Catalog"  # noqa: F821
    versions: CatalogVersions


def compute_versions(catalog) -> CatalogVersions:
    ensure_sp1_on_path(None)
    import semantic_layer

    return CatalogVersions(
        catalog_version="sp1-" + catalog.definitions_version,
        semantic_version=semantic_layer.__version__,
        source_checksum=catalog.definitions_version,
        build_timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def load(definitions_dir: str) -> InternalCatalog:
    ensure_sp1_on_path(None)
    from semantic_layer import load_catalog

    catalog = load_catalog(definitions_dir)
    return InternalCatalog(catalog=catalog, versions=compute_versions(catalog))
