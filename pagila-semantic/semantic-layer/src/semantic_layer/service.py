"""Public entry point: structured request in, guarded read-only result out (spec 5, 10, 13, 14)."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .compiler import Compiled, Compiler
from .errors import SemanticError
from .executor import Executor, PgConfig
from .guard import check_sql
from .loader import Definitions, load_definitions

audit = logging.getLogger("semantic_layer.audit")
DEFAULT_ROOT = Path(__file__).resolve().parents[2]
REDACTED = "<redacted>"


def redact_request(request: Any) -> Any:
    """Copy of the request for the audit line with every filters[].value replaced. Never raises, never mutates."""
    try:
        if not isinstance(request, dict):
            return str(type(request))
        out = dict(request)
        filters = out.get("filters")
        if isinstance(filters, list):
            out["filters"] = [
                {**f, "value": REDACTED} if isinstance(f, dict) and "value" in f else
                (dict(f) if isinstance(f, dict) else REDACTED)
                for f in filters
            ]
        elif "filters" in out:
            out["filters"] = REDACTED
        return out
    except Exception:  # audit must never break the query path
        return "<unavailable>"


class SemanticLayer:
    def __init__(self, root: str | Path | None = None, allow_draft: bool = False,
                 executor: Executor | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.defs: Definitions = load_definitions(self.root, allow_draft=allow_draft)
        self.compiler = Compiler(self.defs)
        self._executor = executor
        self._data_end: str | None = None

    @property
    def executor(self) -> Executor:
        if self._executor is None:
            self._executor = Executor(PgConfig.from_env())
        return self._executor

    # ------------------------------------------------------------------ catalog
    def catalog(self) -> dict:
        """What consumers may ask for: exposed models, measures and dimensions only."""
        out = {}
        for name, model in self.defs.models.items():
            if not self.defs.is_exposed(model.raw):
                continue
            exposed_dims = {n for n, d in model.dimensions.items() if self.defs.is_exposed(d, model.raw)}
            out[name] = {
                "label": model.raw["label"], "description": model.raw["description"], "grain": model.raw["grain"],
                "version": model.version, "time_dimension": model.raw["time_dimension"],
                "measures": [{"name": n, "label": m["label"], "description": m["description"], "unit": m["unit"],
                              "version": m["version"], "combinable_with": m["combinable_with"],
                              "allowed_dimensions": [e for e in m["allowed_dimensions"]
                                                     if e.partition(":")[0] in exposed_dims]}
                             for n, m in model.measures.items() if self.defs.is_exposed(m, model.raw)],
                "dimensions": [{"name": n, "label": d["label"], "description": d["description"], "type": d["type"],
                                "data_type": d["data_type"],
                                **({"granularities": d["granularities"]} if d["type"] == "time" else {})}
                               for n, d in model.dimensions.items() if n in exposed_dims],
            }
        return out

    def policy(self) -> dict:
        """The query policy (single source) as a dict, for /v1/context."""
        return self.defs.policy.as_dict()

    # ------------------------------------------------------------------ execution
    def compile(self, request: dict) -> Compiled:
        compiled = self.compiler.compile(request)
        self.guard(compiled.sql)
        return compiled

    def guard(self, sql: str, include_reference: bool = False, require_limit: bool = True) -> None:
        policy = self.defs.policy
        check_sql(sql, self.defs.table_allow_list(include_reference), self.defs.blocked_columns,
                  max_joins=policy.max_joins, max_limit=policy.max_rows + 1, require_limit=require_limit)

    def execute_sql(self, sql: str, params: list[Any], include_reference: bool = False, require_limit: bool = True):
        """Guard, then run. The only path to the database."""
        self.guard(sql, include_reference, require_limit)
        return self.executor.run(sql, params)

    def data_end(self) -> str:
        if self._data_end is None:
            _, rows = self.execute_sql("SELECT MAX(r.rental_date) FROM public.rental r LIMIT 1", [])
            self._data_end = str(rows[0][0])[:10]
        return self._data_end

    def query(self, request: dict, caller: str = "unknown") -> dict:
        started = time.perf_counter()
        error = None
        compiled = None
        row_count = None
        try:
            compiled = self.compile(request)
            cols, rows = self.execute_sql(compiled.sql, compiled.params)
            truncated = len(rows) > compiled.limit
            rows = rows[:compiled.limit]
            caveats = list(compiled.caveats)
            codes = list(compiled.caveat_codes)
            if truncated:
                caveats.append(f"Result truncated to {compiled.limit} rows.")
                codes.append("truncated")
            if compiled.time_granularity:
                caveats.append(f"Data ends on {self.data_end()}, so the most recent {compiled.time_granularity} may be partial.")
                codes.append("partial_period")
            row_count = len(rows)
            return {
                "model": compiled.model.name,
                "columns": compiled.column_meta,
                "rows": [dict(zip(cols, r)) for r in rows],
                "row_count": row_count,
                "truncated": truncated,
                "sql": compiled.sql,
                "params": compiled.params,
                "definitions_used": compiled.definitions_used,
                "measure_versions": compiled.measure_versions,
                "caveats": caveats,
                "caveat_codes": codes,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
            }
        except SemanticError as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            # Audit: request, versions, SQL, outcome. Never credentials and never returned rows.
            audit.info(json.dumps({
                "caller": caller, "request": redact_request(request),
                "model": compiled.model.name if compiled else None,
                "measures": compiled.measure_names if compiled else [],
                "dimensions": compiled.dimension_names if compiled else [],
                "measure_versions": compiled.measure_versions if compiled else [],
                "definitions": compiled.definitions_used if compiled else None,
                "sql": compiled.sql if compiled else None,
                "row_count": row_count,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1), "error": error,
            }, default=str))
