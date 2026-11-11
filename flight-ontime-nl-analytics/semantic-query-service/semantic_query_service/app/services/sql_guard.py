"""Read-only AST guard (CONTRACT §6.10 I7). sqlglot read="postgres", then SP-1 assert_read_only_sql."""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from ..schemas.errors import SqlGuardRejection
from ..sp1_bridge import ensure_sp1_on_path

BASE_PREFIXES = ("pg_", "lo_", "dblink")
_FORBIDDEN = tuple(getattr(exp, n) for n in (
    "Insert", "Update", "Delete", "Drop", "Create", "Alter", "Command", "Merge", "Into", "Copy",
    "TruncateTable", "Set", "Grant", "Use", "Transaction", "Commit", "Rollback", "Pragma", "AlterTable",
    "Lock", "Analyze", "Kill") if hasattr(exp, n))
_PLACEHOLDER = re.compile(r"%\((\w+)\)s")


def _reject(reason: str) -> SqlGuardRejection:
    return SqlGuardRejection(f"sql guard: {reason}")   # never includes the SQL text


def _policy(policy):
    if policy is None:
        ensure_sp1_on_path()
        from semantic_layer.models import QueryPolicy
        return QueryPolicy()
    return policy


def check(sql: str, policy=None) -> None:
    """Raise SqlGuardRejection unless sql is exactly one read-only SELECT within policy."""
    policy = _policy(policy)
    if not isinstance(sql, str) or not sql.strip():
        raise _reject("empty statement")
    try:
        # %(name)s driver placeholders become sqlglot named placeholders for parsing only
        stmts = [s for s in sqlglot.parse(_PLACEHOLDER.sub(r":\1", sql), read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError:
        raise _reject("unparseable statement") from None
    if len(stmts) != 1:
        raise _reject("exactly one statement is required")
    root = stmts[0]
    if not isinstance(root, exp.Select):
        raise _reject("root statement is not a SELECT")
    for node in root.walk():
        if isinstance(node, _FORBIDDEN):
            raise _reject(f"forbidden construct {type(node).__name__}")
    if root.args.get("into"):
        raise _reject("INTO is not allowed")
    cte_names = set()
    with_ = root.args.get("with_") or root.args.get("with")
    if with_ is not None:
        for cte in with_.expressions:
            if not isinstance(cte.this, exp.Select):
                raise _reject("CTE body is not a SELECT")
            cte_names.add(cte.alias_or_name.lower())
    prefixes = tuple(p.lower() for p in BASE_PREFIXES) + tuple(
        p.lower() for p in getattr(policy, "prohibited_function_prefixes", ()))
    prohibited = {f.lower() for f in getattr(policy, "prohibited_functions", ())}
    allowed = {s.lower() for s in getattr(policy, "allowed_sources", ())}
    for node in root.walk():
        if isinstance(node, exp.Func):
            name = (node.name if isinstance(node, exp.Anonymous) else node.sql_name()).lower()
            if name in prohibited or name.startswith(prefixes):
                raise _reject("prohibited function")
        elif isinstance(node, exp.Table):
            name = ".".join(p for p in (node.args.get("db") and node.db, node.name) if p).lower()
            if node.db or name not in cte_names:
                if name not in allowed:
                    raise _reject("source not allowed")
    if not root.args.get("limit"):
        raise _reject("LIMIT is required")
    ensure_sp1_on_path()
    from semantic_layer.errors import PolicyViolation
    from semantic_layer.policy import assert_read_only_sql
    try:
        assert_read_only_sql(sql, policy)
    except PolicyViolation:
        raise _reject("policy violation") from None
