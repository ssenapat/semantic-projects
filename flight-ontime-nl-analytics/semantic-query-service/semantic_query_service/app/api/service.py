"""Query orchestration: validate -> plan -> compile -> guard -> execute -> format -> audit."""
from __future__ import annotations

import threading
import time
from datetime import date

from pydantic import ValidationError

from ... import __version__
from ..config import Settings
from ..observability.logging import log_event
from ..observability.metrics import Metrics
from ..repositories.internal_semantic_registry import InternalSemanticRegistry
from ..repositories.mapping_repository import MappingRepository
from ..repositories.query_audit_repository import QueryAuditRepository
from ..schemas.errors import (DatabaseUnavailable, QueryExecutionFailed, QueryTimeout, SqlGuardRejection,
                              make_envelope)
from ..schemas.query_plan import ValidatedQueryPlan
from ..schemas.semantic_request import (QueryClarificationResponse, QueryExecuteRequest,
                                        QueryUnsupportedResponse)
from ..services import explain_guard, internal_catalog_loader, query_planner, result_formatter, sql_compiler
from ..services.postgres_executor import PostgresExecutor
from ..services.reference_date import resolve_reference_date
from ..sp1_bridge import ensure_sp1_on_path


class QueryService:
    def __init__(self, settings: Settings, executor=None):
        self.settings = settings
        self.executor = executor
        self.metrics = Metrics()
        self.audit = QueryAuditRepository(settings.query_audit_log_path)
        self.internal = None
        self.registry = None
        self.mappings = MappingRepository(
            [s for s in settings.query_always_clarify_locations.split(",")])
        self.not_ready_code = "catalog_not_ready"
        self.not_ready_detail = "The service is not ready."
        self._lock = threading.Lock()

    # ---- readiness -------------------------------------------------------------------------
    @property
    def reference_date(self) -> date:
        """Today (or QUERY_REFERENCE_DATE), evaluated per request; never derived from the data."""
        return resolve_reference_date(self.settings.query_reference_date)

    @property
    def ready(self) -> bool:
        return self.registry is not None and self.mappings.loaded

    def ensure_ready(self) -> bool:
        """Idempotent; retried on every call until it succeeds. Never raises."""
        with self._lock:
            if self.ready:
                return True
            try:
                if self.internal is None:
                    ensure_sp1_on_path(self.settings.semantic_layer_src)
                    self.internal = internal_catalog_loader.load(self.settings.semantic_layer_definitions_dir)
                    self.registry = InternalSemanticRegistry(self.internal, self.settings.semantic_audience)
                    if self.executor is None:
                        self.executor = PostgresExecutor(self.settings, policy=self.registry.query_policy)
                    elif getattr(self.executor, "policy", 1) is None:
                        self.executor.policy = self.registry.query_policy
            except Exception:  # noqa: BLE001
                self.internal = self.registry = None
                self._set_not_ready("catalog_not_ready", "catalog could not be loaded")
                return False
            try:
                _ = self.reference_date  # validates QUERY_REFERENCE_DATE
                if not self.mappings.loaded:
                    self.mappings.load(self._lookup_fetch)
            except Exception:  # noqa: BLE001
                self._set_not_ready("database_unavailable", self._db_not_ready_detail())
                return False
            return True

    def _set_not_ready(self, code: str, detail: str) -> None:
        """A8: the detail names the variable/cause, never its value; one not_ready log record."""
        self.not_ready_code = code
        self.not_ready_detail = detail
        log_event("ERROR", service_version=__version__, event="not_ready", error_code=code)

    def _db_not_ready_detail(self) -> str:
        configured = self.settings.query_reference_date
        if configured and configured.strip():
            try:
                date.fromisoformat(configured.strip())
            except ValueError:
                return "QUERY_REFERENCE_DATE is invalid (expected YYYY-MM-DD)"
        pw = self.settings.pgpassword
        if isinstance(self.executor, PostgresExecutor) and (pw is None or not pw.get_secret_value()):
            return "PGPASSWORD is not set"
        return "The database is unavailable."

    def _lookup_fetch(self, sql: str, params):
        if " limit " not in sql.lower():
            sql += " LIMIT 100000"   # the guard requires a LIMIT on every statement (I2/I7)
        return self.executor.fetch(sql, params)

    # ---- request handling ------------------------------------------------------------------
    def _err(self, code: str, rid: str, detail: str, started: float, cv: str = "", metrics=None, **kw):
        env = make_envelope(code, rid, detail)
        self.audit_and_log(rid, cv, metrics, {}, [], [], "error:" + code, 0, started, error_code=code)
        return env.status, env.model_dump()

    def audit_and_log(self, rid, cv, metrics, mvs, group_by, fdims, outcome, rows, started, error_code=None):
        ms = int((time.perf_counter() - started) * 1000)
        self.audit.record(request_id=rid, catalog_version=cv, metrics=list(metrics or []),
                          metric_versions=dict(mvs or {}), group_by=group_by,
                          filter_dimensions=fdims, outcome=outcome, row_count=rows, elapsed_ms=ms)
        sv = self.internal.versions.semantic_version if self.internal else None
        log_event("ERROR" if error_code else "INFO", service_version=__version__, request_id=rid,
                  catalog_version=cv, semantic_version=sv, outcome=outcome, latency_ms=ms, event="query_execute",
                  error_code=error_code)

    def execute(self, body: dict, header_request_id: str | None = None) -> tuple[int, dict]:
        started = time.perf_counter()
        self.metrics.inc("query_service_requests_total")
        rid = (body.get("request_id") if isinstance(body, dict) and isinstance(body.get("request_id"), str)
               else None) or header_request_id or "unknown"
        try:
            req = QueryExecuteRequest.model_validate(body)
        except ValidationError:
            self.metrics.inc("query_service_validation_failures_total")
            return self._err("query_validation_failed", rid, "The request body failed schema validation.", started)
        requested = list(req.semantic_request.metrics)
        if not self.ensure_ready():
            return self._err(self.not_ready_code, rid, self.not_ready_detail, started, metrics=requested)
        cv = self.internal.versions.catalog_version
        if req.catalog_version != cv:
            return self._err("catalog_version_mismatch", rid, "The request catalog_version does not match.",
                             started, cv, metrics=requested)
        plan = query_planner.build_plan(req, self.registry, self.mappings, self.reference_date)
        if not isinstance(plan, ValidatedQueryPlan):
            return self._semantic_outcome(plan, rid, cv, started, requested=requested)
        compiled = sql_compiler.compile_plan(plan, self.internal, self.settings.semantic_audience)
        if isinstance(compiled, (QueryClarificationResponse, QueryUnsupportedResponse)):
            return self._semantic_outcome(compiled, rid, cv, started, plan, requested)
        metrics = [self.registry.metric(n) for n in plan.metrics]
        fdims = [f["dimension"] for f in plan.resolved_filters]
        gnames = [g["sp1_term"] for g in plan.group_by]
        explain_checked = False
        try:
            if self.settings.query_explain_enabled:
                explain_checked = explain_guard.check_cost(self.executor.explain_cost, compiled.sql,
                                                           compiled.params, self.settings.query_explain_max_cost)
            t0 = time.perf_counter()
            rows = self.executor.fetch(compiled.sql, compiled.params)
            self.metrics.set("query_service_db_latency_ms", int((time.perf_counter() - t0) * 1000))
        except SqlGuardRejection:
            self.metrics.inc("query_service_sql_guard_rejections_total")
            return self._err("sql_guard_rejected", rid, "The compiled query was rejected by the SQL guard.",
                             started, cv)
        except QueryTimeout:
            return self._err("query_timeout", rid, "The query exceeded the statement timeout.", started, cv)
        except DatabaseUnavailable:
            return self._err("database_unavailable", rid, "The database is unavailable.", started, cv)
        except QueryExecutionFailed:
            return self._err("query_execution_failed", rid, "The query failed to execute.", started, cv)
        elapsed = int((time.perf_counter() - started) * 1000)
        resp = result_formatter.format_result(
            plan=plan, registry=self.registry, metrics=metrics, raw_rows=rows, elapsed_ms=elapsed,
            effective_limit=compiled.effective_limit, reference_date=self.reference_date,
            explain_checked=explain_checked, max_payload_bytes=self.settings.query_max_payload_bytes)
        self.metrics.inc("query_service_result_rows_total", resp.row_count)
        self.audit_and_log(rid, cv, plan.metrics, plan.metric_versions, gnames, fdims, "result",
                           resp.row_count, started)
        return 200, resp.model_dump()

    def _semantic_outcome(self, out, rid, cv, started, plan=None, requested=None):
        if isinstance(out, QueryClarificationResponse):
            self.metrics.inc("query_service_clarification_total")
            outcome = "clarification_required"
        else:
            outcome = "unsupported"
        self.audit_and_log(rid, cv, plan.metrics if plan else requested,
                           plan.metric_versions if plan else {}, [], [], outcome, 0, started)
        return 200, out.model_dump()
