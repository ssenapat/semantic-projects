"""Policy rules (CONTRACT C-5): read-only SQL assertion and default-deny helpers."""
from __future__ import annotations

import re
from typing import Iterable

from .errors import PolicyViolation
from .models import (
    DEFAULT_ALLOWED_STATEMENT_TYPES,
    DEFAULT_PROHIBITED_FUNCTION_PREFIXES,
    DEFAULT_PROHIBITED_FUNCTIONS,
    DEFAULT_PROHIBITED_KEYWORDS,
    Catalog,
    QueryPolicy,
)

_IDENT_CHAR = re.compile(r"[A-Za-z0-9_$]")
_DOLLAR_TAG = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_CALL = re.compile(r"([A-Za-z_][A-Za-z0-9_$]*)\s*\(")
_STAR_PATTERNS = (
    re.compile(r"\bSELECT\s+(?:(?:DISTINCT|ALL)\s+)?\*", re.IGNORECASE),
    re.compile(r",\s*\*"),
    re.compile(r"(?:[A-Za-z_][A-Za-z0-9_$]*|\"_\")\s*\.\s*\*"),
)


def strip_sql(sql: str) -> str:
    """Remove comments; blank string literals to ``''`` and quoted identifiers to ``"_"``."""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        two = sql[i:i + 2]
        if two == "--":
            j = sql.find("\n", i)
            i = n if j == -1 else j
            out.append(" ")
        elif two == "/*":
            depth, j = 1, i + 2
            while j < n and depth:
                if sql[j:j + 2] == "/*":
                    depth += 1
                    j += 2
                elif sql[j:j + 2] == "*/":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            if depth:
                raise PolicyViolation("unterminated_literal", "unterminated block comment")
            i = j
            out.append(" ")
        elif ch == "'":
            escape = bool(out) and out[-1] in ("E", "e") and (
                len(out) < 2 or not _IDENT_CHAR.match(out[-2][-1:] or " ")
            )
            if escape:
                out.pop()
            j = i + 1
            while True:
                if j >= n:
                    raise PolicyViolation("unterminated_literal", "unterminated string literal")
                if escape and sql[j] == "\\":
                    j += 2
                    continue
                if sql[j] == "'":
                    if sql[j + 1:j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append("''")
            i = j + 1
        elif ch == "$" and not (out and _IDENT_CHAR.match(out[-1][-1:] or " ")):
            m = _DOLLAR_TAG.match(sql, i)
            if m is None:
                out.append(ch)
                i += 1
                continue
            j = sql.find(m.group(0), m.end())
            if j == -1:
                raise PolicyViolation("unterminated_literal", "unterminated dollar-quoted string")
            out.append("''")
            i = j + len(m.group(0))
        elif ch == '"':
            j = i + 1
            while True:
                if j >= n:
                    raise PolicyViolation("unterminated_literal", "unterminated quoted identifier")
                if sql[j] == '"':
                    if sql[j + 1:j + 2] == '"':
                        j += 2
                        continue
                    break
                j += 1
            out.append('"_"')
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def assert_read_only_sql(sql: str, qp: QueryPolicy | None = None) -> None:
    """Allow exactly one SELECT/WITH statement; raise PolicyViolation otherwise (C-5.1)."""
    if qp is not None:
        allowed_types = {t.upper() for t in qp.allowed_statement_types}
        keywords = {k.upper() for k in qp.prohibited_keywords}
        functions = {f.lower() for f in qp.prohibited_functions}
        prefixes = tuple(p.lower() for p in qp.prohibited_function_prefixes)
    else:
        allowed_types = set(DEFAULT_ALLOWED_STATEMENT_TYPES)
        keywords = set(DEFAULT_PROHIBITED_KEYWORDS)
        functions = set(DEFAULT_PROHIBITED_FUNCTIONS)
        prefixes = DEFAULT_PROHIBITED_FUNCTION_PREFIXES

    stripped = strip_sql(sql).strip()
    if not stripped:
        raise PolicyViolation("empty_statement", "empty statement")
    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
        if not stripped:
            raise PolicyViolation("empty_statement", "empty statement")
    if ";" in stripped:
        raise PolicyViolation("multiple_statements", "only a single statement is allowed")

    first = _WORD.match(stripped)
    if first is None or first.group(0).upper() not in allowed_types:
        raise PolicyViolation(
            "statement_type_not_allowed", "only SELECT or WITH statements are allowed"
        )
    for word in _WORD.findall(stripped):
        if word.upper() in keywords:
            raise PolicyViolation("prohibited_keyword", f"prohibited keyword: {word.upper()}")
    for name in _CALL.findall(stripped):
        low = name.lower()
        if low in functions or (prefixes and low.startswith(prefixes)):
            raise PolicyViolation("prohibited_function", f"prohibited function: {low}")
    if any(p.search(stripped) for p in _STAR_PATTERNS):
        raise PolicyViolation("select_star", "SELECT * is not allowed; list columns explicitly")


def check_physical_object(
    catalog: Catalog, physical_object: str, purpose: str = "query"
) -> None:
    """Default-deny for physical objects (C-5.2)."""
    qp = catalog.query_policy
    allowed = set(qp.allowed_sources)
    if purpose == "reconciliation":
        allowed |= set(qp.reconciliation_only_sources)
    if purpose not in ("query", "reconciliation") or physical_object not in allowed:
        raise PolicyViolation(
            "object_not_allowed", f"object not allowed for purpose {purpose!r}: {physical_object}"
        )


def find_prohibited_columns(
    patterns: Iterable[str], column_names: Iterable[str]
) -> list[str]:
    """Column names containing any pattern (case-insensitive substring), in input order."""
    pats = [p.lower() for p in patterns if p]
    found: list[str] = []
    for name in column_names:
        low = str(name).lower()
        if any(p in low for p in pats) and name not in found:
            found.append(name)
    return found


def normalize_term(term: str) -> str:
    """lowercase; runs of characters outside [a-z0-9] -> '_'; strip leading/trailing '_'."""
    return re.sub(r"[^a-z0-9]+", "_", term.lower()).strip("_")
