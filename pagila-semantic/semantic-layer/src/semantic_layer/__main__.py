"""Command line: python -m semantic_layer {catalog,compile,query,validate} ..."""
from __future__ import annotations

import argparse
import json
import logging
import sys

from .errors import SemanticError
from .service import SemanticLayer


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="semantic_layer", description="Read-only Pagila semantic layer")
    p.add_argument("--root", help="semantic-layer directory (default: this package's project)")
    p.add_argument("--allow-draft", action="store_true", help="also expose draft definitions (development only)")
    p.add_argument("--audit", action="store_true", help="print the audit log to stderr")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("catalog", help="list exposed models, measures and dimensions")
    for name in ("compile", "query"):
        s = sub.add_parser(name, help=f"{name} a JSON request (file path or - for stdin)")
        s.add_argument("request")
    sub.add_parser("validate", help="run validation and reconciliation checks and record the result")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if args.audit:
        logging.getLogger("semantic_layer.audit").setLevel(logging.INFO)
        logging.getLogger("semantic_layer.audit").addHandler(logging.StreamHandler(sys.stderr))
    try:
        layer = SemanticLayer(root=args.root, allow_draft=args.allow_draft or args.cmd == "validate")
        if args.cmd == "catalog":
            print(json.dumps(layer.catalog(), indent=2))
        elif args.cmd in ("compile", "query"):
            text = sys.stdin.read() if args.request == "-" else open(args.request).read()
            request = json.loads(text)
            if args.cmd == "compile":
                c = layer.compile(request)
                print(json.dumps({"sql": c.sql, "params": c.params, "caveats": c.caveats, "caveat_codes": c.caveat_codes,
                                  "columns": c.column_meta, "measure_versions": c.measure_versions}, indent=2, default=str))
            else:
                print(json.dumps(layer.query(request, caller="cli"), indent=2, default=str))
        else:
            from .validate import run_all
            report = run_all(layer)
            for section in ("sql_checks", "reconciliations", "probes"):
                for r in report[section]:
                    mark = "PASS" if r["passed"] else ("FAIL" if r["severity"] == "error" else "INFO")
                    detail = "" if r["passed"] else f"  actual={r.get('actual')} expected={r.get('expected')} {r.get('error', '')}"
                    print(f"{mark}  {r['check']}: {r['label']}{detail}")
            print(f"\n{report['passed']}/{report['total']} passed, {report['failed_errors']} failing errors, "
                  f"{report['failed_info']} informational differences")
            return 0 if report["all_error_checks_passed"] else 1
    except (SemanticError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
