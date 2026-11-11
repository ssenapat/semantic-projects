"""Validator CLI: ``python -m semantic_layer.validator`` (CONTRACT C-1, C-4, C-5.3).

Offline checks (references, policies, metric catalog, SQL read-only, secrets) never open a
database connection. Live checks (validation SQL, metric reconciliation, live schema,
discovery) run only through ``pg_client.ReadOnlyConnection`` (invariants I1, I2, I12).
The validator writes nothing to disk and nothing to a database: stdout/stderr only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import re
import sys
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from . import pg_client
from .compiler import compile_request, execute
from .errors import ConfigError, DatabaseError, PolicyViolation, SemanticLayerError
from .loader import check_references, load_catalog, metric_definition_hash
from .models import DEFAULT_PROHIBITED_KEYWORDS, Catalog, Metric
from .policy import assert_read_only_sql, find_prohibited_columns, strip_sql

EXIT_OK, EXIT_FAIL, EXIT_USAGE, EXIT_CONFIG = 0, 1, 2, 3

# --------------------------------------------------------------------------- data classes


@dataclass
class CheckResult:
    name: str
    status: str  # pass | fail | warn | skip
    details: list[str]
    offenders: list[str]
    data: dict | None = None


@dataclass(frozen=True)
class ValidationBlock:
    file: str
    name: str
    description: str | None
    expect: dict | None
    expect_rows: int | None
    tolerance: float
    severity: str
    sql: str


class ValidationSqlError(ValueError):
    """A validation SQL file does not follow the C-4.1 block format."""


# --------------------------------------------------------------------------- C-4.1 parsing

_CHECK_RE = re.compile(r"^\s*--\s*@check:\s*(.*?)\s*$")
_DIRECTIVE_RE = re.compile(r"^\s*--\s*@(description|expect_rows|expect|tolerance|severity):\s*(.*?)\s*$")
_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def parse_validation_sql(path: str | Path) -> list[ValidationBlock]:
    """Parse one ``*.sql`` file into blocks (C-4.1). Raises ``ValidationSqlError``."""
    p = Path(path)
    fname = p.name
    raw: list[dict] = []
    cur: dict | None = None
    for lineno, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        m = _CHECK_RE.match(line)
        if m:
            cur = {"name": m.group(1), "line": lineno, "directives": {}, "sql": []}
            raw.append(cur)
            continue
        if cur is None:
            continue  # optional file header comments
        d = _DIRECTIVE_RE.match(line)
        if d:
            key, value = d.group(1), d.group(2)
            if key in cur["directives"]:
                raise ValidationSqlError(f"{fname}:{lineno}: duplicate @{key} in block {cur['name']}")
            cur["directives"][key] = value
            continue
        if line.lstrip().startswith("--"):
            continue  # plain comment line
        cur["sql"].append(line)

    blocks: list[ValidationBlock] = []
    seen: set[str] = set()
    for b in raw:
        name, where = b["name"], f"{fname}:{b['line']}"
        if not _NAME_RE.match(name):
            raise ValidationSqlError(f"{where}: @check name {name!r} is not snake_case")
        if name in seen:
            raise ValidationSqlError(f"{where}: duplicate @check name {name!r}")
        seen.add(name)
        dr = b["directives"]
        if ("expect" in dr) == ("expect_rows" in dr):
            raise ValidationSqlError(f"{where}: block {name} needs exactly one of @expect / @expect_rows")
        expect: dict | None = None
        expect_rows: int | None = None
        if "expect" in dr:
            try:
                expect = json.loads(dr["expect"])
            except json.JSONDecodeError as exc:
                raise ValidationSqlError(f"{where}: block {name} @expect is not valid JSON ({exc.msg})") from None
            if not isinstance(expect, dict) or not expect:
                raise ValidationSqlError(f"{where}: block {name} @expect must be a non-empty JSON object")
        else:
            try:
                expect_rows = int(dr["expect_rows"])
            except ValueError:
                raise ValidationSqlError(f"{where}: block {name} @expect_rows must be an integer") from None
            if expect_rows < 0:
                raise ValidationSqlError(f"{where}: block {name} @expect_rows must be >= 0")
        try:
            tolerance = float(dr["tolerance"]) if "tolerance" in dr else 0.0
        except ValueError:
            raise ValidationSqlError(f"{where}: block {name} @tolerance must be a number") from None
        if tolerance < 0:
            raise ValidationSqlError(f"{where}: block {name} @tolerance must be >= 0")
        severity = dr.get("severity", "error")
        if severity not in ("error", "warn"):
            raise ValidationSqlError(f"{where}: block {name} @severity must be error or warn")
        sql = "\n".join(b["sql"]).strip()
        if not sql:
            raise ValidationSqlError(f"{where}: block {name} has no SQL statement")
        if not sql.endswith(";"):
            raise ValidationSqlError(f"{where}: block {name} statement must be terminated by ';'")
        blocks.append(ValidationBlock(
            file=fname, name=name, description=dr.get("description") or None, expect=expect,
            expect_rows=expect_rows, tolerance=tolerance, severity=severity, sql=sql[:-1].rstrip()))
    return blocks


# --------------------------------------------------------------------------- comparison (C-4.1)


def _fmt(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float, Decimal)) and not isinstance(x, bool)


def _matches(expected: Any, actual: Any, tolerance: float) -> bool:
    if expected is None:
        return actual is None
    if _is_num(expected):
        if not _is_num(actual):
            return False
        try:
            return abs(Decimal(str(actual)) - Decimal(str(expected))) <= Decimal(str(tolerance))
        except InvalidOperation:
            return False
    if isinstance(actual, (dt.datetime, dt.date)):
        actual = actual.isoformat()
    return expected == actual


def compare_expectation(block: ValidationBlock, columns: list[str], rows: list[tuple]) -> tuple[bool, list[str]]:
    """Compare a result with the block expectation. Returns ``(ok, details)``."""
    if block.expect_rows is not None:
        if len(rows) == block.expect_rows:
            return True, [f"rows={len(rows)}"]
        return False, [f"expected rows={block.expect_rows} actual {len(rows)}"]
    expect = block.expect or {}
    if len(rows) != 1:
        return False, [f"expected 1 row actual {len(rows)}"]
    row = rows[0]
    index = {c: i for i, c in enumerate(columns)}
    details: list[str] = []
    for key, exp in expect.items():
        if key not in index or index[key] >= len(row):
            details.append(f"expected {key}={_fmt(exp)} actual <missing column>")
            continue
        act = row[index[key]]
        if not _matches(exp, act, block.tolerance):
            details.append(f"expected {key}={_fmt(exp)} actual {_fmt(act)}")
    return (not details), (details or [f"{len(expect)} value(s) match"])


# --------------------------------------------------------------------------- helpers


def _sql_files(sql_dir: str | Path) -> list[Path]:
    d = Path(sql_dir)
    return sorted((p for p in d.glob("*.sql") if p.is_file()), key=lambda p: p.name) if d.is_dir() else []


def _result(name: str, offenders: list[str], ok_detail: str, fail_detail: str | None = None,
            data: dict | None = None) -> CheckResult:
    offenders = sorted(set(offenders))
    if offenders:
        return CheckResult(name, "fail", [fail_detail or f"{len(offenders)} offender(s)"], offenders, data)
    return CheckResult(name, "pass", [ok_detail], [], data)


def _blocks_of(sql_dir: str | Path) -> tuple[list[ValidationBlock], list[str]]:
    """All blocks of all files (sorted by filename) and parse-error offenders."""
    blocks: list[ValidationBlock] = []
    errors: list[str] = []
    for f in _sql_files(sql_dir):
        try:
            blocks.extend(parse_validation_sql(f))
        except (ValidationSqlError, OSError, UnicodeDecodeError) as exc:
            errors.append(f"{f.name}:parse_error:{exc}")
    return blocks, errors


# --------------------------------------------------------------------------- offline checks


def run_references(catalog: Catalog, sql_dir: str | Path) -> CheckResult:
    offenders = check_references(catalog, sql_dir)
    return _result("references", list(offenders), "rules R1-R17: 0 offenders",
                   f"{len(set(offenders))} reference offender(s)")


def _referenced_columns(catalog: Catalog) -> dict[str, set[str]]:
    """source name -> columns referenced anywhere in the definitions (C-1.5, C-1.7)."""
    used: dict[str, set[str]] = {}

    def add(source: str, column: str | None) -> None:
        if column and column != "*":
            used.setdefault(source, set()).add(column)

    def add_ref(ref: str) -> None:
        src, _, col = ref.partition(".")
        add(src, col)

    for s in catalog.sources.values():
        for k in s.primary_key:
            add(s.name, k)
        add(s.name, s.time_column)
    for model in catalog.models.values():
        p = model.primary_source
        for e in model.entities:
            add(p, e.column)
            if e.references:
                add_ref(e.references)
        for d in model.dimensions:
            add(d.source, d.column)
        for m in model.measures:
            add(p, m.expression)
            if m.filter:
                add(p, m.filter.column)
        for x in model.excluded_columns:
            for c in x.columns:
                add(x.source, c)
    for met in catalog.metrics.values():
        src = met.source or ""
        for f in met.filters or ():
            add(src, f.column)
    for r in catalog.relationships.values():
        add_ref(r.left)
        add_ref(r.right)
    return used


def run_policies(catalog: Catalog) -> CheckResult:
    bad: list[str] = []
    dp, qp = catalog.data_policy, catalog.query_policy
    if dp.default != "deny":
        bad.append(f"data_policy.default:must be deny, found {dp.default!r}")
    for s in catalog.sources.values():
        if dp.classification_defaults.get(s.physical_object) != "internal":
            bad.append(f"data_policy.classification_defaults:{s.physical_object} must be internal")
    used = _referenced_columns(catalog)
    for src in sorted(used):
        for col in find_prohibited_columns(dp.prohibited_column_patterns, sorted(used[src])):
            bad.append(f"prohibited_column:{src}.{col}:matches a prohibited_column_patterns entry")

    def need(cond: bool, rule: str, msg: str) -> None:
        if not cond:
            bad.append(f"query_policy.{rule}:{msg}")

    need(qp.database_access == "read_only", "database_access", "must be read_only")
    need(set(qp.allowed_statement_types) <= {"SELECT", "WITH"}, "allowed_statement_types", "must be a subset of SELECT, WITH")
    required_stmt = {"INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE", "GRANT",
                     "REVOKE", "COPY", "CALL", "DO"}
    missing = sorted(required_stmt - {x.upper() for x in qp.prohibited_statement_types})
    need(not missing, "prohibited_statement_types", f"missing {', '.join(missing)}")
    missing = sorted(set(DEFAULT_PROHIBITED_KEYWORDS) - {x.upper() for x in qp.prohibited_keywords})
    need(not missing, "prohibited_keywords", f"missing {', '.join(missing)}")
    need(qp.max_result_rows <= 1000, "max_result_rows", f"must be <= 1000, found {qp.max_result_rows}")
    need(1 <= qp.default_limit <= qp.max_result_rows, "default_limit",
         f"must satisfy 1 <= default_limit <= max_result_rows, found {qp.default_limit}")
    need(qp.statement_timeout_seconds == 30, "statement_timeout_seconds",
         f"must be 30, found {qp.statement_timeout_seconds}")
    need(qp.prohibit_raw_sql_from_consumers is True, "prohibit_raw_sql_from_consumers", "must be true")
    need(qp.require_metric_allowlist is True, "require_metric_allowlist", "must be true")
    missing = sorted({"delay_direction_unspecified", "airport_role_unspecified"} - set(qp.clarify_when))
    need(not missing, "clarify_when", f"missing {', '.join(missing)}")
    need(qp.audiences.get("consumer") == "approved", "audiences.consumer.min_status", "must be approved")
    if catalog.connection.read_only is not True:
        bad.append("sources.connection.read_only:must be true")
    if catalog.connection.credentials != "env":
        bad.append("sources.connection.credentials:must be env")
    return _result("policies", bad, "default-deny, read-only and query limits hold",
                   f"{len(set(bad))} policy offender(s)")


_MISSING_ATTRS = ("name", "label", "description", "status", "version", "business_owner", "technical_owner",
                  "source", "grain", "expression", "unit", "time_basis", "null_handling", "filters",
                  "allowed_dimensions", "validation_sql", "reconciliation", "change_log")
_DOC_PREFIXES = ("- Label:", "- Status:", "- Version:", "- Business owner:", "- Technical owner:",
                 "- Business definition:", "- Source / grain / time basis:", "- Formula:",
                 "- Null handling and exclusions:", "- Allowed dimensions:", "- Known caveats:",
                 "- Validation SQL and reconciliation:", "- Approval:", "- Change log:")


def _attr_missing(met: Metric, attr: str) -> bool:
    v = getattr(met, attr, None)
    if attr == "filters":
        return v is None  # key must be present; the list may be empty
    if attr == "reconciliation":
        return v is None or v.baseline is None or not v.reference
    if v is None:
        return True
    if isinstance(v, (str, tuple, list, dict)) and len(v) == 0:
        return True
    return False


def _doc_sections(text: str) -> tuple[dict[str, list[list[str]]], list[str]]:
    """Level-2 headings -> list of body-line lists (one per occurrence); every heading in order."""
    sections: dict[str, list[list[str]]] = {}
    headings: list[str] = []
    in_fence = False
    cur: list[str] | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        m = None if in_fence else re.match(r"^## (.*?)\s*$", line)
        if m:
            headings.append(m.group(1))
            cur = []
            sections.setdefault(m.group(1), []).append(cur)
        elif cur is not None:
            cur.append(line)
    return sections, headings


def run_metric_catalog(catalog: Catalog, doc_path: str | Path) -> list[CheckResult]:
    bad: list[str] = []
    metrics = sorted(catalog.metrics.values(), key=lambda m: m.name)
    for met in metrics:
        if met.status in ("validated", "approved"):
            attrs = list(_MISSING_ATTRS) + (["numerator", "denominator"] if met.type == "ratio" else [])
            for a in attrs:
                if _attr_missing(met, a):
                    bad.append(f"{met.name}:missing_attribute:{a}")
    doc = Path(doc_path)
    if not doc.is_file():
        bad.append(f"{doc_path}:file_not_found")
    else:
        sections, headings = _doc_sections(doc.read_text(encoding="utf-8"))
        names = {m.name for m in metrics}
        for h in sorted(set(headings) - names):
            bad.append(f"{h}:heading_is_not_a_metric")
        for met in metrics:
            occ = sections.get(met.name, [])
            if not occ:
                bad.append(f"{met.name}:missing_heading")
                continue
            if len(occ) > 1:
                bad.append(f"{met.name}:duplicate_heading")
            body = occ[0]
            for prefix in _DOC_PREFIXES:
                if not any(line.startswith(prefix) for line in body):
                    bad.append(f"{met.name}:missing_line:{prefix}")
            for prefix, expected in (("- Status:", met.status), ("- Version:", None if met.version is None else str(met.version))):
                line = next((x for x in body if x.startswith(prefix)), None)
                if line is not None and line[len(prefix):].strip() != expected:
                    bad.append(f"{met.name}:doc_mismatch:{prefix[2:-1]} doc={line[len(prefix):].strip()!r} yaml={expected!r}")
    cat_res = _result("metric_catalog", bad, f"{len(metrics)} metric(s) documented and complete",
                      f"{len(set(bad))} metric catalog offender(s)")

    vbad: list[str] = []
    vdetails: list[str] = []
    for met in metrics:
        log = met.change_log or ()
        if not log:
            vbad.append(f"{met.name}:change_log_empty")
            continue
        versions = [e.version for e in log]
        if any(b <= a for a, b in zip(versions, versions[1:])):
            vbad.append(f"{met.name}:change_log_versions_not_increasing")
        if met.version is None or versions[-1] != met.version:
            vbad.append(f"{met.name}:change_log_last_version_mismatch")
        try:
            computed = metric_definition_hash(met, catalog.model_for(met))
        except SemanticLayerError as exc:
            vbad.append(f"{met.name}:hash_unavailable:{exc}")
            continue
        if log[-1].definition_hash != computed:
            vbad.append(f"metric_changed_without_version_bump:{met.name}")
            vdetails.append(f"{met.name}: computed {computed} recorded {log[-1].definition_hash}")
    ver_res = _result("metric_versions", vbad, f"{len(metrics)} metric(s) versioned consistently",
                      f"{len(set(vbad))} metric version offender(s)")
    if vbad:
        ver_res.details = sorted(vdetails) or ver_res.details
    return [cat_res, ver_res]


_NONDETERMINISTIC = re.compile(
    r"\b(now|random|clock_timestamp|statement_timestamp|transaction_timestamp|timeofday|current_date|"
    r"current_time|current_timestamp|localtime|localtimestamp|gen_random_uuid)\b", re.I)


def run_sql_readonly(sql_dir: str | Path) -> CheckResult:
    d = Path(sql_dir)
    if not d.is_dir():
        return CheckResult("sql_readonly", "fail", ["sql directory not found"], [f"{d}:sql_dir_not_found"])
    files = _sql_files(d)
    if not files:
        return CheckResult("sql_readonly", "fail", ["no *.sql files found"], [f"{d}:no_sql_files"])
    blocks, bad = _blocks_of(d)
    for b in blocks:
        ident = f"{Path(b.file).stem}.{b.name}"
        try:
            assert_read_only_sql(b.sql)
        except PolicyViolation as exc:
            bad.append(f"{ident}:{exc.code}")
            continue
        hit = _NONDETERMINISTIC.search(strip_sql(b.sql))
        if hit:
            bad.append(f"{ident}:nondeterministic_function:{hit.group(1).lower()}")
    return _result("sql_readonly", bad, f"{len(blocks)} block(s) in {len(files)} file(s) are read-only",
                   f"{len(set(bad))} read-only offender(s)")


# --------------------------------------------------------------------------- secrets (C-5.3)

_SECRET_PATTERNS = (
    ("S1", re.compile(r"postgres(?:ql)?://[^\s:/@]+:[^\s@]+@", re.I)),
    ("S2", re.compile(r"\b(?:PGPASSWORD|TRANSPORT_STATS_PG_PASSWORD|POSTGRES_PASSWORD)\s*=\s*"
                      r"(?![\s'\"]*(?:\$|<|$))['\"]?[^\s'\"]+", re.I)),
    ("S3", re.compile(r"\bpassword\s*[:=]\s*['\"][^'\"\s]{3,}['\"]", re.I)),
)
_SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".git"}


def _scan_files(root: Path) -> Iterable[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.endswith(".egg-info"))
        for fn in sorted(filenames):
            yield Path(dirpath) / fn


def check_secrets(root: str | Path, env_password: str | Iterable[str] | None = None,
                  ignore_values: Iterable[str] = ()) -> CheckResult:
    """Scan text files under ``root`` for credential literals; matched values are never printed."""
    base = Path(root)
    if not base.is_dir():
        return CheckResult("secrets", "fail", ["scan root not found"], [f"{base}:root_not_found"])
    if env_password is None:
        passwords: list[str] = []
    elif isinstance(env_password, str):
        passwords = [env_password]
    else:
        passwords = list(env_password)
    common = {v for v in ignore_values if v} | {"postgres"}
    skipped = False
    literals: list[str] = []
    for pw in passwords:
        if not pw or len(pw) < 4:
            continue
        if pw in common:
            skipped = True
            continue
        if pw.lower() not in literals:
            literals.append(pw.lower())

    offenders: set[str] = set()
    scanned = 0
    for path in _scan_files(base):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data[:8192]:
            continue  # binary
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        scanned += 1
        rel = path.relative_to(base).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for pid, rx in _SECRET_PATTERNS:
                if rx.search(line):
                    offenders.add(f"{rel}:{lineno}:{pid}")
            if literals:
                low = line.lower()
                if any(lit in low for lit in literals):
                    offenders.add(f"{rel}:{lineno}:S4")
    ordered = sorted(offenders)
    if ordered:
        return CheckResult("secrets", "fail", [f"{len(ordered)} credential literal(s) found"], ordered)
    if skipped:
        return CheckResult("secrets", "warn", ["password equals a common identifier; literal scan skipped"], [])
    return CheckResult("secrets", "pass", [f"no credential literals in {scanned} file(s)"], [])


# --------------------------------------------------------------------------- live checks

_NULL_LOGGER = logging.getLogger("semantic_layer.validator.silent")
_NULL_LOGGER.addHandler(logging.NullHandler())
_NULL_LOGGER.propagate = False


def _db_error(exc: BaseException) -> str:
    return f"database error: {exc}"


def run_validation_sql(conn: Any, sql_dir: str | Path) -> list[CheckResult]:
    out: list[CheckResult] = []
    blocks, errors = _blocks_of(sql_dir)
    for e in errors:
        fname = e.split(":", 1)[0]
        out.append(CheckResult(f"validation_sql:{Path(fname).stem}", "fail", [e], [e]))
    if not blocks and not errors:
        return [CheckResult("validation_sql", "skip", [f"no validation SQL found in {sql_dir}"], [])]
    for b in blocks:
        name = f"validation_sql:{Path(b.file).stem}.{b.name}"
        try:
            res = conn.fetch(b.sql)
        except (DatabaseError, PolicyViolation) as exc:
            out.append(CheckResult(name, "warn" if b.severity == "warn" else "fail", [_db_error(exc)], []))
            continue
        ok, details = compare_expectation(b, list(res.columns), [tuple(r) for r in res.rows])
        status = "pass" if ok else ("warn" if b.severity == "warn" else "fail")
        out.append(CheckResult(name, status, details, []))
    return out


def run_reconcile(conn: Any, catalog: Catalog) -> list[CheckResult]:
    out: list[CheckResult] = []
    metrics = sorted((m for m in catalog.metrics.values() if m.reconciliation is not None), key=lambda m: m.name)
    if not metrics:
        return [CheckResult("reconcile", "skip", ["no metric has a reconciliation block"], [])]
    for met in metrics:
        name = f"reconcile:{met.name}"
        rec = met.reconciliation
        resp = compile_request({"metric": met.name}, catalog, audience="reconciliation", logger=_NULL_LOGGER)
        if resp.status != "ok":
            status = "skip" if resp.reason_code == "metric_not_exposable" else "fail"
            out.append(CheckResult(name, status, [f"compile {resp.status}: {resp.reason_code}"], []))
            continue
        try:
            result = execute(resp, conn, logger=_NULL_LOGGER)
        except (DatabaseError, PolicyViolation) as exc:
            out.append(CheckResult(name, "fail", [_db_error(exc)], []))
            continue
        if result.row_count != 1 or not result.rows:
            out.append(CheckResult(name, "fail", [f"expected 1 row actual {result.row_count}"], []))
            continue
        row = result.rows[0]
        actual = row[met.name] if met.name in row else next(iter(row.values()), None)
        tol = rec.tolerance if rec.tolerance is not None else 0
        detail = f"expected {_fmt(rec.baseline)} actual {_fmt(actual)} tolerance {_fmt(tol)}"
        try:
            ok = _is_num(actual) and _is_num(rec.baseline) and \
                abs(Decimal(str(actual)) - Decimal(str(rec.baseline))) <= Decimal(str(tol))
        except InvalidOperation:
            ok = False
        out.append(CheckResult(name, "pass" if ok else "fail", [detail], []))
    return out


def _live_columns(conn: Any) -> dict[str, list[str]]:
    res = conn.fetch(
        "SELECT table_name, column_name FROM information_schema.columns "
        "WHERE table_schema = 'ontime' ORDER BY table_name, column_name")
    live: dict[str, list[str]] = {}
    for table, column in res.rows:
        live.setdefault(str(table), []).append(str(column))
    return {t: sorted(c) for t, c in sorted(live.items())}


def run_live_schema(conn: Any, catalog: Catalog) -> list[CheckResult]:
    names = ("live_schema.columns_exist", "live_schema.coverage", "live_schema.prohibited_columns")
    try:
        live = _live_columns(conn)
    except (DatabaseError, PolicyViolation) as exc:
        return [CheckResult(n, "fail", [_db_error(exc)], []) for n in names]
    used = _referenced_columns(catalog)
    by_table = {s.physical_object.split(".", 1)[1]: s for s in catalog.sources.values()
                if s.physical_object.startswith("ontime.")}

    missing: list[str] = []
    for src_name in sorted(used):
        src = catalog.sources.get(src_name)
        if src is None:
            continue
        table = src.physical_object.split(".", 1)[1]
        if table not in live:
            missing.append(f"{src.physical_object}:object_missing")
            continue
        for col in sorted(used[src_name]):
            if col not in live[table]:
                missing.append(f"{src.physical_object}.{col}:column_missing")
    exists = _result(names[0], missing, "every referenced column exists", f"{len(set(missing))} missing")

    uncovered: list[str] = []
    undeclared: list[str] = []
    for table, cols in live.items():
        src = by_table.get(table)
        if src is None:
            undeclared.append(f"ontime.{table}:object_not_declared")
            continue
        if src.usage == "reconciliation_only":
            continue
        for col in cols:
            if col not in used.get(src.name, set()):
                uncovered.append(f"{src.physical_object}.{col}:column_not_covered")
    if uncovered:
        coverage = CheckResult(names[1], "fail", [f"{len(set(uncovered))} live column(s) not covered"],
                               sorted(set(uncovered) | set(undeclared)))
    elif undeclared:
        coverage = CheckResult(names[1], "warn", ["live object(s) not declared as a source (default-deny keeps them unqueryable)"],
                               sorted(set(undeclared)))
    else:
        coverage = CheckResult(names[1], "pass", ["every live ontime column is accounted for"], [])

    patterns = catalog.data_policy.prohibited_column_patterns
    hard: list[str] = []
    soft: list[str] = []
    for table, cols in live.items():
        src = by_table.get(table)
        for col in find_prohibited_columns(patterns, cols):
            ident = f"ontime.{table}.{col}:matches a prohibited_column_patterns entry"
            if src is not None and col in used.get(src.name, set()):
                hard.append(ident)
            else:
                soft.append(ident)
    if hard:
        prohibited = CheckResult(names[2], "fail", [f"{len(set(hard))} referenced column(s) match prohibited patterns"],
                                 sorted(set(hard) | set(soft)))
    elif soft:
        prohibited = CheckResult(names[2], "warn", ["live column(s) match prohibited patterns but are not referenced"],
                                 sorted(set(soft)))
    else:
        prohibited = CheckResult(names[2], "pass", ["no live column matches a prohibited pattern"], [])
    return [exists, coverage, prohibited]


# -- discovery (C-1.7; §6.6 queries verbatim)
_Q_OBJECTS = ("SELECT table_schema, table_name, table_type FROM information_schema.tables "
              "WHERE table_schema NOT IN ('pg_catalog', 'information_schema') ORDER BY 1, 2")
_Q_COLUMNS = ("SELECT table_name, ordinal_position, column_name, data_type, character_maximum_length, is_nullable "
              "FROM information_schema.columns WHERE table_schema = 'ontime' ORDER BY table_name, ordinal_position")
_Q_CONSTRAINTS = ("SELECT conrelid::regclass AS table_name, conname, contype, pg_get_constraintdef(oid) AS definition "
                  "FROM pg_catalog.pg_constraint WHERE connamespace = 'ontime'::regnamespace ORDER BY 1, 2")
_Q_APPROX = ("SELECT c.relname, c.reltuples::bigint AS approx_rows FROM pg_catalog.pg_class c "
             "WHERE c.relnamespace = 'ontime'::regnamespace AND c.relkind = 'r'")
_Q_DATES = "SELECT MIN(fl_date) AS first_date, MAX(fl_date) AS last_date, COUNT(*) AS rows FROM ontime.flight"
_Q_SERVER = "SELECT current_setting('server_version') AS server_version, current_database() AS current_database"
_Q_CANDIDATES = ("SELECT datname FROM pg_catalog.pg_database WHERE datallowconn AND NOT datistemplate ORDER BY 1")
_Q_HAS_FLIGHT = "SELECT to_regclass('ontime.flight') IS NOT NULL AS has_ontime_flight"


def _json_safe(v: Any) -> Any:
    if isinstance(v, bool) or v is None or isinstance(v, (int, str)):
        return v
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, float):
        return v
    return str(v)


def _error_class(exc: BaseException) -> str:
    m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):", str(exc))
    return m.group(1) if isinstance(exc, DatabaseError) and m else type(exc).__name__


def run_discover(conn: Any, settings: Any) -> CheckResult:
    details: list[str] = []
    data: dict[str, Any] = {}
    try:
        server = conn.fetch(_Q_SERVER)
        data["server_version"] = str(server.rows[0][0])
        data["current_database"] = str(server.rows[0][1])
        names = [str(r[0]) for r in conn.fetch(_Q_CANDIDATES).rows]
    except (DatabaseError, PolicyViolation) as exc:
        return CheckResult("discover", "fail", [_db_error(exc)], [])

    candidates = []
    for name in names:
        entry: dict[str, Any] = {"name": name, "has_ontime_flight": False, "error": None}
        try:
            with pg_client.connect(settings.with_database(name)) as cconn:
                entry["has_ontime_flight"] = bool(cconn.fetch(_Q_HAS_FLIGHT).rows[0][0])
        except Exception as exc:  # noqa: BLE001 - only the class name is recorded
            entry["error"] = _error_class(exc)
        candidates.append(entry)
    data["database_candidates"] = candidates

    try:
        objects = [{"schema": str(r[0]), "name": str(r[1]), "type": str(r[2])} for r in conn.fetch(_Q_OBJECTS).rows]
        columns = [{"table": str(r[0]), "ordinal": _json_safe(r[1]), "column": str(r[2]), "data_type": str(r[3]),
                    "max_length": _json_safe(r[4]), "is_nullable": str(r[5])} for r in conn.fetch(_Q_COLUMNS).rows]
        constraints = [{"table": str(r[0]), "name": str(r[1]), "type": str(r[2]), "definition": str(r[3])}
                       for r in conn.fetch(_Q_CONSTRAINTS).rows]
        approx = [{"name": str(r[0]), "approx_rows": _json_safe(r[1])} for r in conn.fetch(_Q_APPROX).rows]
    except (DatabaseError, PolicyViolation) as exc:
        data.update({"objects": [], "columns": [], "constraints": [], "approx_rows": [],
                     "exact_counts": {}, "date_coverage": None})
        return CheckResult("discover", "fail", [_db_error(exc)], ["discovery_queries_failed"], data)
    data["objects"] = sorted(objects, key=lambda o: (o["schema"], o["name"]))
    data["columns"] = sorted(columns, key=lambda c: (c["table"], c["ordinal"] if c["ordinal"] is not None else 0))
    data["constraints"] = sorted(constraints, key=lambda c: (c["table"], c["name"]))
    data["approx_rows"] = sorted(approx, key=lambda a: a["name"])

    counts: dict[str, Any] = {}
    for o in data["objects"]:
        if o["schema"] != "ontime":
            continue
        ident = o["name"].replace('"', '""')
        try:
            counts[o["name"]] = _json_safe(conn.fetch(f'SELECT COUNT(*) AS n FROM "ontime"."{ident}"').rows[0][0])
        except (DatabaseError, PolicyViolation):
            counts[o["name"]] = None
    data["exact_counts"] = dict(sorted(counts.items()))

    try:
        r = conn.fetch(_Q_DATES).rows[0]
        data["date_coverage"] = {"first_date": _json_safe(r[0]), "last_date": _json_safe(r[1]), "rows": _json_safe(r[2])}
    except (DatabaseError, PolicyViolation):
        data["date_coverage"] = None

    with_flight = [c["name"] for c in candidates if c["has_ontime_flight"]]
    details.append(f"server {data['server_version']}; configured database {data['current_database']}; "
                   f"candidates with ontime.flight: {', '.join(with_flight) or 'none'}")
    if not with_flight:
        return CheckResult("discover", "fail", details, ["no_candidate_database_has_ontime.flight"], data)
    if data["current_database"] not in with_flight:
        details.append(f"configured database lacks ontime.flight; set TRANSPORT_STATS_PG_DATABASE={with_flight[0]}")
        return CheckResult("discover", "warn", details, [], data)
    return CheckResult("discover", "pass", details, [], data)


def run_session_check(conn: Any) -> CheckResult:
    try:
        s = conn.session_settings()
    except (DatabaseError, PolicyViolation) as exc:
        return CheckResult("session_read_only", "fail", [_db_error(exc)], [])
    ro, to = s.get("transaction_read_only"), s.get("statement_timeout")
    detail = f"transaction_read_only={ro} statement_timeout={to}"
    ok = ro == "on" and to == "30s"
    return CheckResult("session_read_only", "pass" if ok else "fail", [detail], [])


# --------------------------------------------------------------------------- report / CLI


def _report(checks: list[CheckResult], version: str | None) -> dict:
    items = []
    for c in sorted(checks, key=lambda c: c.name):
        item: dict[str, Any] = {"name": c.name, "status": c.status, "details": list(c.details),
                                "offenders": list(c.offenders)}
        if c.data is not None:
            item["data"] = c.data
        items.append(item)
    return {"ok": not any(c.status == "fail" for c in checks), "definitions_version": version, "checks": items}


def _render(report: dict, as_json: bool) -> str:
    if as_json:
        return json.dumps(report, sort_keys=True, indent=2, ensure_ascii=True) + "\n"
    lines: list[str] = []
    for c in report["checks"]:
        first = c["details"][0] if c["details"] else ""
        lines.append(f"{c['status'].upper()} {c['name']}: {first}".rstrip())
        extra = c["details"][1:] if c["status"] != "pass" else []
        for text in extra + c["offenders"]:
            lines.append(f"  {text}")
    return "\n".join(lines) + ("\n" if lines else "")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m semantic_layer.validator", allow_abbrev=False,
                                description="Validate the semantic-layer definitions (read-only).")
    p.add_argument("--definitions", default="definitions/", help="definitions directory (default definitions/)")
    p.add_argument("--check-references", action="store_true", help="rules R1-R17")
    p.add_argument("--check-policies", action="store_true", help="data and query policy rules")
    p.add_argument("--check-metric-catalog", metavar="PATH", help="docs/metric-catalog.md vs definitions")
    p.add_argument("--check-sql-readonly", action="store_true", help="every validation SQL block is read-only")
    p.add_argument("--check-secrets", nargs="?", const="", default=None, metavar="ROOT",
                   help="scan for credential literals (default: parent of --definitions)")
    p.add_argument("--sql-dir", default="validation/", help="validation SQL directory (default validation/)")
    p.add_argument("--run-validation-sql", action="store_true", help="live: run validation SQL blocks")
    p.add_argument("--reconcile-metrics", action="store_true", help="live: reconcile metrics with baselines")
    p.add_argument("--check-live-schema", action="store_true", help="live: columns, coverage, prohibited columns")
    p.add_argument("--discover", action="store_true", help="live: source discovery inventory")
    p.add_argument("--connection", choices=["env"], help="connection source; only 'env'")
    p.add_argument("--json", action="store_true", help="print the report as JSON")
    return p


def _err(message: str) -> None:
    print(message, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_USAGE

    offline = (args.check_references or args.check_policies or args.check_metric_catalog is not None
               or args.check_sql_readonly or args.check_secrets is not None)
    live = args.run_validation_sql or args.reconcile_metrics or args.check_live_schema or args.discover
    if not offline and not live:
        _err("no checks selected")
        return EXIT_USAGE
    if live and args.connection != "env":
        _err("--connection env is required with --run-validation-sql, --reconcile-metrics, "
             "--check-live-schema and --discover")
        return EXIT_USAGE

    checks: list[CheckResult] = []
    try:
        catalog = load_catalog(args.definitions)
    except Exception as exc:  # noqa: BLE001 - any load failure is exit 2
        checks.append(CheckResult("load_definitions", "fail", [str(exc)], []))
        sys.stdout.write(_render(_report(checks, None), args.json))
        return EXIT_USAGE
    checks.append(CheckResult("load_definitions", "pass", [f"definitions_version {catalog.definitions_version}"], []))

    conn = None
    settings = None
    if live:
        try:
            settings = pg_client.ConnectionSettings.from_env(catalog.connection.database)
            conn = pg_client.connect(settings)
        except (ConfigError, DatabaseError) as exc:
            _err(f"{type(exc).__name__}: {exc}")
            if args.discover and isinstance(exc, DatabaseError):
                _err("If the configured database does not exist, re-run with TRANSPORT_STATS_PG_DATABASE=postgres")
            return EXIT_CONFIG

    try:
        if args.check_references:
            checks.append(run_references(catalog, args.sql_dir))
        if args.check_policies:
            checks.append(run_policies(catalog))
        if args.check_metric_catalog is not None:
            checks.extend(run_metric_catalog(catalog, args.check_metric_catalog))
        if args.check_sql_readonly:
            checks.append(run_sql_readonly(args.sql_dir))
        if args.check_secrets is not None:
            root = Path(args.check_secrets) if args.check_secrets else Path(args.definitions).resolve().parent
            passwords = [v for v in (os.environ.get("TRANSPORT_STATS_PG_PASSWORD"), os.environ.get("PGPASSWORD")) if v]
            common = [os.environ.get("TRANSPORT_STATS_PG_USER"), os.environ.get("PGUSER"),
                      os.environ.get("TRANSPORT_STATS_PG_DATABASE"), catalog.connection.database]
            checks.append(check_secrets(root, passwords, [c for c in common if c]))
        if conn is not None:
            checks.append(run_session_check(conn))
            if args.run_validation_sql:
                checks.extend(run_validation_sql(conn, args.sql_dir))
            if args.reconcile_metrics:
                checks.extend(run_reconcile(conn, catalog))
            if args.check_live_schema:
                checks.extend(run_live_schema(conn, catalog))
            if args.discover:
                checks.append(run_discover(conn, settings))
    finally:
        if conn is not None:
            conn.close()

    report = _report(checks, catalog.definitions_version)
    sys.stdout.write(_render(report, args.json))
    return EXIT_OK if report["ok"] else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
