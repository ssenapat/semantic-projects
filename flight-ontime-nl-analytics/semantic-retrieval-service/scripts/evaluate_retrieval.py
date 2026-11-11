"""Evaluate retrieval on airline paraphrases (REQ-031). Exit 1 when pass rate < --min-pass-rate."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from _common import settings


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-pass-rate", type=float, default=0.9)
    ap.add_argument("--dataset", default=str(Path(__file__).with_name("eval_questions.yml")))
    args = ap.parse_args()
    from semantic_retrieval_service.app.services import catalog_loader
    from semantic_retrieval_service.app.services.context_retriever import ContextRetriever, NoRetrievableContext

    s = settings()
    try:
        loaded = catalog_loader.load(s["definitions_dir"], s["audience"])
        retriever = ContextRetriever(loaded, max_context_tokens=s["max_tokens"], vector_enabled=s["vector_enabled"],
                                     audience=s["audience"], embedding_provider="hash")
        cases = yaml.safe_load(Path(args.dataset).read_text(encoding="utf-8"))["questions"]
    except (catalog_loader.CatalogLoadError, ValueError, OSError, KeyError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    passed = 0
    for case in cases:
        try:
            _, summary = retriever.retrieve(case["question"], [], "eval")
            got_metrics, got_dims = summary.metrics, summary.dimensions
            terms, triggers = summary.glossary_terms, summary.ambiguity_triggers
            matched = set(summary.match_sources)
            ok = (not case.get("expect_no_context")
                  and all(m in got_metrics for m in case.get("metrics", []))
                  and all(d in got_dims for d in case.get("dimensions", []))
                  and all(t in terms for t in case.get("glossary_terms", []))
                  and all(t in triggers for t in case.get("ambiguity_triggers", []))
                  and all(t in matched for t in case.get("matched", []))
                  and all(m in got_metrics[: case.get("top", 8)] for m in case.get("top_metrics", [])))
        except NoRetrievableContext:
            ok = bool(case.get("expect_no_context"))
        passed += ok
        print(("PASS " if ok else "FAIL ") + case["question"])
    rate = passed / len(cases) if cases else 0.0
    print(f"pass rate {passed}/{len(cases)} = {rate:.2f} (min {args.min_pass_rate})")
    return 0 if rate >= args.min_pass_rate else 1


if __name__ == "__main__":
    sys.exit(main())
