"""SQL guard (spec 14.3): only one read-only SELECT over allow-listed objects may run."""
from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .errors import GuardError

# Anonymous (non-standard) functions that must never appear. Matched by prefix, lowercase.
DENIED_FUNCTION_PREFIXES = (
    "pg_terminate", "pg_cancel", "pg_reload", "pg_sleep", "pg_read", "pg_ls", "pg_advisory", "pg_stat_file",
    "pg_logical", "pg_create", "pg_drop", "pg_switch", "pg_promote", "set_config", "nextval", "setval",
    "currval", "lastval", "lo_", "dblink", "query_to_xml", "table_to_xml", "xpath", "txid_",
)
WRITE_NODES = tuple(
    getattr(exp, n) for n in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable", "Command", "Copy", "Set",
        "TruncateTable", "Grant", "Revoke", "Use", "Transaction", "Commit", "Rollback", "Execute", "Pragma",
        "Into", "Lock",
    ) if hasattr(exp, n)
)


def check_sql(sql: str, allowed_tables: set[str], blocked_columns: set[str], *, max_joins: int,
              max_limit: int, require_limit: bool = True) -> None:
    """Raise GuardError unless `sql` is a single safe SELECT. Bind placeholders (%s) are allowed."""
    try:
        statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
    except ParseError as exc:
        raise GuardError(f"SQL could not be parsed: {exc}") from exc
    if len(statements) != 1:
        raise GuardError("exactly one SQL statement is allowed")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise GuardError(f"only SELECT statements are allowed, got {type(tree).__name__}")

    for node in tree.find_all(*WRITE_NODES):
        raise GuardError(f"{type(node).__name__} is not allowed in a read-only query")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if name in cte_names and not table.db:
            continue
        if table.db and table.db.lower() != "public":
            raise GuardError(f"schema {table.db!r} is not allowed")
        if name not in allowed_tables:
            raise GuardError(f"table {table.name!r} is not in the allow-list")

    for col in tree.find_all(exp.Column):
        if col.name.lower() in blocked_columns:
            raise GuardError(f"column {col.name!r} is personal or secret data and cannot be queried")
    if any(isinstance(s, exp.Star) for s in tree.find_all(exp.Star)):
        # SELECT * would leak blocked columns; COUNT(*) is a Star inside a function and is fine.
        for star in tree.find_all(exp.Star):
            if not isinstance(star.parent, (exp.Count, exp.Func)):
                raise GuardError("SELECT * is not allowed; name the columns")

    for func in tree.find_all(exp.Anonymous):
        fname = func.name.lower()
        if fname.startswith(DENIED_FUNCTION_PREFIXES):
            raise GuardError(f"function {func.name!r} is not allowed")

    if len(list(tree.find_all(exp.Join))) > max_joins:
        raise GuardError(f"more than {max_joins} joins")

    if require_limit:
        limit = tree.args.get("limit")
        if limit is None:
            raise GuardError("a LIMIT is required")
        try:
            n = int(limit.expression.name)
        except (ValueError, AttributeError):
            raise GuardError("LIMIT must be an integer literal") from None
        if n < 1 or n > max_limit:
            raise GuardError(f"LIMIT {n} is outside 1..{max_limit}")
