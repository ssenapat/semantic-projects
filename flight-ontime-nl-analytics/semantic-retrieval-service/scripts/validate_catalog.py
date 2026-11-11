"""Validate the SP-1 catalog for retrieval (REQ-017). Exit 0 = valid, 1 = invalid."""
from __future__ import annotations

import sys

from _common import settings


def main() -> int:
    from semantic_retrieval_service.app.services import catalog_loader

    s = settings()
    try:
        loaded = catalog_loader.load(s["definitions_dir"], s["audience"])
    except catalog_loader.CatalogLoadError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    counts: dict[str, int] = {}
    for d in loaded.documents:
        counts[d.document_type] = counts.get(d.document_type, 0) + 1
    print(f"OK catalog_version={loaded.versions.catalog_version} documents={len(loaded.documents)} {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
