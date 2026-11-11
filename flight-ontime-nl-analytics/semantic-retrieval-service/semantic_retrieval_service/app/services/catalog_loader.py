"""Loads the SP-1 catalog, validates it and builds eligible SemanticDocuments (CONTRACT §6.1, §7.1)."""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass

from ..schemas.catalog import SemanticDocument
from ..sp1_bridge import default_src, ensure_sp1_on_path, resolve_path


class CatalogLoadError(Exception):
    """The catalog could not be loaded or failed validation."""


@dataclass(frozen=True)
class CatalogVersions:
    catalog_version: str  # "sp1-" + catalog.definitions_version
    semantic_version: str  # semantic_layer.__version__
    source_checksum: str  # catalog.definitions_version (sha256[:12] over definitions/*.yml)
    build_timestamp: str  # UTC load time


@dataclass
class LoadedCatalog:
    catalog: "semantic_layer.models.Catalog"  # noqa: F821
    versions: CatalogVersions
    documents: list[SemanticDocument]


def compute_versions(catalog) -> CatalogVersions:
    import semantic_layer

    return CatalogVersions(
        catalog_version="sp1-" + catalog.definitions_version,
        semantic_version=semantic_layer.__version__,
        source_checksum=catalog.definitions_version,
        build_timestamp=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )


def _defect(exc: Exception) -> str:
    """Name the catalog defect (REQ-010/013, A8) without paths: file reduced to its base name."""
    file = getattr(exc, "file", None)
    if file is None:
        return type(exc).__name__
    loc = getattr(exc, "location", "")
    msg = getattr(exc, "message", str(exc))
    return f"{os.path.basename(str(file))}:{loc}: {msg}"


def load(definitions_dir: str, audience: str) -> LoadedCatalog:
    from . import catalog_validator, document_normalizer

    try:
        ensure_sp1_on_path(default_src())
        from semantic_layer.errors import SemanticLayerError
        from semantic_layer.loader import load_catalog
    except (ImportError, FileNotFoundError) as exc:
        raise CatalogLoadError(f"semantic layer unavailable: {type(exc).__name__}") from exc
    try:
        catalog = load_catalog(resolve_path(definitions_dir))
    except SemanticLayerError as exc:
        raise CatalogLoadError("catalog invalid: " + _defect(exc)) from exc
    errors = catalog_validator.validate(catalog, audience)
    if errors:
        raise CatalogLoadError("catalog invalid: " + "; ".join(errors[:10]))
    versions = compute_versions(catalog)
    documents = document_normalizer.normalize(catalog, versions, audience)
    return LoadedCatalog(catalog=catalog, versions=versions, documents=documents)
