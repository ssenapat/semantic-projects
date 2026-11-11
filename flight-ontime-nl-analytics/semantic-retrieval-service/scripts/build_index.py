"""Build index artifacts (REQ-017/079). ``--with-vectors`` also writes the vector artifacts."""
from __future__ import annotations

import argparse
import json
import sys

from _common import settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-vectors", action="store_true")
    args = ap.parse_args()
    from semantic_retrieval_service.app.repositories import artifact_repository as repo
    from semantic_retrieval_service.app.services import catalog_loader
    from semantic_retrieval_service.app.services.vector_retriever import VectorRetriever

    s = settings()
    try:
        loaded = catalog_loader.load(s["definitions_dir"], s["audience"])
        out = repo.artifacts_dir(s["artifacts_dir"])
        repo.write_documents(out, loaded.documents)
        v = loaded.versions
        manifest = {
            "catalog_version": v.catalog_version, "semantic_version": v.semantic_version,
            "source_checksum": v.source_checksum, "build_timestamp": v.build_timestamp,
            "eligibility_audience": s["audience"], "document_count": len(loaded.documents),
            "vector_enabled": bool(args.with_vectors),
        }
        if args.with_vectors:
            try:
                import faiss  # noqa: F401
                import numpy as np
            except ImportError:
                print("FAIL: --with-vectors needs faiss and numpy installed", file=sys.stderr)
                return 1
            provider = "hash" if s["provider"] in ("none", "hash", "local") else s["provider"]
            ids, vecs = VectorRetriever(loaded.documents, enabled=True, provider=provider).vectors()
            index = faiss.IndexFlatIP(len(vecs[0]))
            index.add(np.array(vecs, dtype="float32"))
            faiss.write_index(index, str(out / repo.VECTORS_FAISS))
            (out / repo.VECTORS_META).write_text(
                json.dumps({"catalog_version": v.catalog_version, "provider": provider, "document_ids": ids}, indent=2) + "\n",
                encoding="utf-8")
        repo.write_manifest(out, manifest)
    except (catalog_loader.CatalogLoadError, ValueError, OSError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print(f"OK built {manifest['document_count']} documents for {manifest['catalog_version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
