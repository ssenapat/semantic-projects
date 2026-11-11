"""Validation and reconciliation runner (spec section 12). Read-only; records results as evidence."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import SemanticError
from .service import SemanticLayer


def _num(v: Any) -> Any:
    return float(v) if isinstance(v, (int, float)) or hasattr(v, "as_integer_ratio") else v


def _close(actual: Any, expected: Any, tol: float) -> bool:
    try:
        return abs(float(actual) - float(expected)) <= float(tol)
    except (TypeError, ValueError):
        return actual == expected


def run_sql_checks(layer: SemanticLayer) -> list[dict]:
    results = []
    for path in sorted((layer.root / "validation").glob("*.sql")):
        text = path.read_text()
        severity = (re.search(r"--\s*severity:\s*(\w+)", text) or [None, "error"])[1]
        description = (re.search(r"--\s*description:\s*(.+)", text) or [None, ""])[1].strip()
        cols, rows = layer.execute_sql(text, [], include_reference=True, require_limit=False)
        for label, actual, expected, tol in rows:
            results.append({"check": path.stem, "label": label, "actual": actual, "expected": expected,
                            "tolerance": tol, "passed": _close(actual, expected, tol), "severity": severity,
                            "description": description})
    return results


def _q(layer: SemanticLayer, request: dict) -> list[dict]:
    return layer.query({**request, "limit": 10_000}, caller="validation")["rows"]


def _scalar(layer: SemanticLayer, sql: str, include_reference: bool = False) -> Any:
    return layer.execute_sql(sql, [], include_reference=include_reference, require_limit=False)[1][0][0]


def run_probes(layer: SemanticLayer) -> list[dict]:
    """Every exposed measure and dimension compiles, passes the guard and runs on the real schema."""
    results = []
    for mname, model in layer.defs.models.items():
        if not layer.defs.is_exposed(model.raw):
            continue
        measures = layer.compiler.exposed_measures(model)
        for m in measures:
            results.append(_probe(layer, f"{mname}.measure.{m}", {"model": mname, "measures": [m], "limit": 1}))
        for d in layer.compiler.exposed_dimensions(model):
            dspec = d + (":month" if model.dimensions[d]["type"] == "time" else "")
            results.append(_probe(layer, f"{mname}.dimension.{d}",
                                  {"model": mname, "measures": measures[:1], "dimensions": [dspec], "limit": 1}))
        for m in measures:   # allocated variant of each measure
            cat = next((d for d, v in model.dimensions.items() if v.get("allocated")), None)
            if cat:
                results.append(_probe(layer, f"{mname}.measure.{m}.by_category",
                                      {"model": mname, "measures": [m], "dimensions": [cat], "limit": 1}))
    return results


def _probe(layer: SemanticLayer, label: str, request: dict) -> dict:
    try:
        layer.query(request, caller="validation")
        return {"check": "probe", "label": label, "passed": True, "severity": "error"}
    except Exception as exc:  # noqa: BLE001 - report every failure, do not stop
        return {"check": "probe", "label": label, "passed": False, "severity": "error", "error": str(exc)[:300]}


def run_reconciliations(layer: SemanticLayer) -> list[dict]:
    out: list[dict] = []

    def add(check: str, label: str, actual: Any, expected: Any, tol: float = 0.01, severity: str = "error", note: str = ""):
        out.append({"check": check, "label": label, "actual": actual, "expected": expected, "tolerance": tol,
                    "passed": _close(actual, expected, tol), "severity": severity, "description": note})

    total = float(_scalar(layer, "SELECT SUM(amount) FROM public.payment"))
    rentals = int(_scalar(layer, "SELECT COUNT(*) FROM public.rental"))

    by_month = _q(layer, {"model": "payment_activity", "measures": ["total_revenue"], "dimensions": ["rental_date:month"]})
    add("X_revenue_by_rental_month_sums", "revenue by rental month adds up to total",
        sum(r["total_revenue"] for r in by_month), total, 0.01, note="spec 7.2.1")

    by_cat = _q(layer, {"model": "payment_activity", "measures": ["total_revenue"], "dimensions": ["payment_category"]})
    add("X_allocated_revenue_sum", "allocated category revenue adds up to total",
        sum(r["total_revenue"] for r in by_cat), total, 0.01, note="spec 7.4")
    add("X_allocated_revenue_sum", "category count", len(by_cat), 16, 0)

    rent_cat = _q(layer, {"model": "rental_activity", "measures": ["rental_count"], "dimensions": ["category"]})
    add("X_allocated_rentals_sum", "allocated category rentals add up to total",
        sum(r["rental_count"] for r in rent_cat), rentals, 0.01, note="spec 7.4")

    by_store = _q(layer, {"model": "payment_activity", "measures": ["total_revenue"], "dimensions": ["payment_store"]})
    ref_store = layer.execute_sql("SELECT total_sales FROM public.sales_by_store ORDER BY total_sales LIMIT 100", [],
                                  include_reference=True)[1]
    mine = sorted(r["total_revenue"] for r in by_store)
    ref = sorted(float(r[0]) for r in ref_store)
    add("V06_reference_sales_by_store", "store count matches Pagila sales_by_store", len(mine), len(ref), 0)
    for i, (a, b) in enumerate(zip(mine, ref)):
        add("V06_reference_sales_by_store", f"store revenue #{i + 1} (sorted) matches Pagila", a, b, 0.01, note="spec 12")

    ref_cat = {r[0]: float(r[1]) for r in layer.execute_sql(
        "SELECT category, total_sales FROM public.sales_by_film_category LIMIT 100", [], include_reference=True)[1]}
    mine_cat = {r["payment_category"]: r["total_revenue"] for r in by_cat}
    over = [c for c, v in mine_cat.items() if c in ref_cat and v > ref_cat[c] + 0.01]
    add("V06_reference_sales_by_film_category", "allocated category revenue never exceeds Pagila's full-count view",
        len(over), 0, 0, note="Pagila counts a film fully in every category, so it is expected to be higher")
    add("V06_reference_sales_by_film_category", "Pagila view over-counts total (informational)",
        round(sum(ref_cat.values()), 2), total, 0.01, severity="info",
        note="expected to exceed the total because films have several categories")

    unpaid = _scalar(layer, "SELECT COUNT(*) FROM public.rental r WHERE NOT EXISTS (SELECT 1 FROM public.payment p WHERE p.rental_id = r.rental_id)")
    add("X_unpaid_rentals_have_no_revenue", "paid rentals + unpaid rentals = all rentals",
        int(_scalar(layer, "SELECT COUNT(DISTINCT rental_id) FROM public.payment")) + int(unpaid), rentals, 0)
    return out


def run_all(layer: SemanticLayer, write: bool = True) -> dict:
    sections = {"sql_checks": run_sql_checks(layer), "reconciliations": run_reconciliations(layer),
                "probes": run_probes(layer)}
    everything = [r for rs in sections.values() for r in rs]
    failed = [r for r in everything if not r["passed"] and r["severity"] == "error"]
    report = {
        "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database": layer.executor.config.database,
        "total": len(everything), "passed": sum(1 for r in everything if r["passed"]),
        "failed_errors": len(failed), "failed_info": sum(1 for r in everything if not r["passed"] and r["severity"] != "error"),
        "all_error_checks_passed": not failed,
        **sections,
    }
    if write:
        out = layer.root / "validation" / "results" / "latest.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, default=str) + "\n")
    return report
