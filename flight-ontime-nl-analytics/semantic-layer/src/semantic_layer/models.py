"""Frozen dataclasses for the semantic catalog (CONTRACT C-3, C-6.1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import DefinitionError

STATUS_ORDER = {"draft": 0, "validated": 1, "approved": 2}

DEFAULT_PROHIBITED_STATEMENT_TYPES = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP",
    "TRUNCATE", "GRANT", "REVOKE", "COPY", "CALL", "DO",
)
DEFAULT_PROHIBITED_KEYWORDS = (
    "INSERT", "UPDATE", "DELETE", "MERGE", "CREATE", "ALTER", "DROP", "TRUNCATE",
    "GRANT", "REVOKE", "COPY", "CALL", "DO", "INTO", "SET", "RESET", "LOCK",
    "VACUUM", "ANALYZE", "REFRESH", "LISTEN", "NOTIFY", "UNLISTEN", "PREPARE",
    "EXECUTE", "DEALLOCATE", "DISCARD", "COMMENT", "REINDEX", "CLUSTER", "IMPORT",
    "SECURITY", "CHECKPOINT", "LOAD", "BEGIN", "COMMIT", "ROLLBACK", "SAVEPOINT",
    "SHARE",
)
DEFAULT_PROHIBITED_FUNCTIONS = (
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
    "set_config", "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "pg_stat_file", "nextval", "setval", "txid_current", "pg_switch_wal",
    "pg_create_restore_point", "pg_promote", "pg_logical_emit_message", "query_to_xml",
)
DEFAULT_PROHIBITED_FUNCTION_PREFIXES = ("lo_", "dblink", "pg_advisory")
DEFAULT_ALLOWED_STATEMENT_TYPES = ("SELECT", "WITH")
DEFAULT_ALLOWED_SOURCES = ("ontime.flight", "ontime.carrier", "ontime.airport")
DEFAULT_RECONCILIATION_ONLY_SOURCES = (
    "ontime.v_carrier_monthly_performance", "ontime.v_route_summary",
)
DEFAULT_AUDIENCES = {"consumer": "approved", "internal": "validated", "reconciliation": "draft"}


@dataclass(frozen=True)
class Source:
    name: str
    physical_object: str
    object_type: str
    usage: str
    grain: str
    classification: str
    status: str
    freshness: str
    primary_key: tuple[str, ...] = ()
    business_owner: str | None = None
    technical_owner: str | None = None
    time_column: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class ConnectionDef:
    engine: str
    database: str
    default_schema: str
    credentials: str
    read_only: bool


@dataclass(frozen=True)
class Entity:
    name: str
    type: str
    column: str
    references: str | None = None


@dataclass(frozen=True)
class Dimension:
    name: str
    label: str
    type: str
    data_type: str
    column: str
    source: str
    status: str
    role: str | None = None
    granularities: tuple[str, ...] = ()
    values: Any = None  # dict (map) | tuple (list) | None
    null_policy: str = "none"
    mapping: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class MeasureFilter:
    column: str
    operator: str
    value: Any  # int | tuple[int, ...] (kept as written; validated by check_references R6)


@dataclass(frozen=True)
class Measure:
    name: str
    aggregation: str
    expression: str
    filter: MeasureFilter | None = None


@dataclass(frozen=True)
class ExcludedColumn:
    source: str
    columns: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SemanticModel:
    name: str
    label: str
    status: str
    primary_source: str
    grain: str
    time_dimension: str | None
    entities: tuple[Entity, ...]
    dimensions: tuple[Dimension, ...]
    measures: tuple[Measure, ...]
    excluded_columns: tuple[ExcludedColumn, ...] = ()

    def dimension(self, name: str) -> Dimension | None:
        for d in self.dimensions:
            if d.name == name:
                return d
        return None

    def measure(self, name: str) -> Measure | None:
        for m in self.measures:
            if m.name == name:
                return m
        return None


@dataclass(frozen=True)
class Reconciliation:
    baseline: Any = None
    scope: str | None = None
    as_of: str | None = None
    tolerance: Any = None
    reference: str | None = None


@dataclass(frozen=True)
class Approval:
    status: str | None = None
    approver: str | None = None
    approved_on: str | None = None


@dataclass(frozen=True)
class ChangeLogEntry:
    version: int
    date: str
    change: str
    definition_hash: str


@dataclass(frozen=True)
class Metric:
    """Attributes other than ``name`` may be None when absent from the YAML, so that
    ``validator --check-metric-catalog`` (C-1.6) can report missing attributes."""
    name: str
    label: str | None = None
    description: str | None = None
    status: str | None = None
    version: int | None = None
    business_owner: str | None = None
    technical_owner: str | None = None
    model: str | None = None
    source: str | None = None
    grain: str | None = None
    type: str | None = None
    measure: str | None = None
    numerator: str | None = None
    denominator: str | None = None
    expression: str | None = None
    filters: tuple[MeasureFilter, ...] | None = None
    null_handling: str | None = None
    allowed_dimensions: tuple[str, ...] | None = None
    unit: str | None = None
    time_basis: str | None = None
    validation_sql: str | None = None
    reconciliation: Reconciliation | None = None
    approval: Approval | None = None
    open_question: str | None = None
    caveats: tuple[str, ...] = ()
    change_log: tuple[ChangeLogEntry, ...] | None = None
    combinable_with: str | None = None  # C-3.2: metrics sharing a value may be queried together


@dataclass(frozen=True)
class Relationship:
    name: str
    left: str
    right: str
    type: str
    join: str
    status: str
    role: str | None = None
    enforced_fk: bool = False
    verified: dict | None = None


@dataclass(frozen=True)
class ProhibitedJoin:
    source: str
    column: str
    reason: str


@dataclass(frozen=True)
class CodeMapping:
    name: str
    status: str
    keys_from: str
    values: dict = field(default_factory=dict)
    unmapped_policy: str = "show_code"


@dataclass(frozen=True)
class DataPolicy:
    default: str
    pii: str
    classification_defaults: dict
    prohibited_column_patterns: tuple[str, ...]


@dataclass(frozen=True)
class AmbiguousTerm:
    reason_code: str
    terms: tuple[str, ...]
    options: tuple[str, ...] = ()
    question: str | None = None  # C-3.1 (D1): clarification question shown to the user


@dataclass(frozen=True)
class GlossaryTerm:
    term: str
    meaning: str
    maps_to: tuple[str, ...]


@dataclass(frozen=True)
class UnavailableConcept:
    reason_code: str
    reason: str
    terms: tuple[str, ...]


@dataclass(frozen=True)
class QueryPolicy:
    database_access: str = "read_only"
    allowed_statement_types: tuple[str, ...] = DEFAULT_ALLOWED_STATEMENT_TYPES
    prohibited_statement_types: tuple[str, ...] = DEFAULT_PROHIBITED_STATEMENT_TYPES
    prohibited_keywords: tuple[str, ...] = DEFAULT_PROHIBITED_KEYWORDS
    prohibited_functions: tuple[str, ...] = DEFAULT_PROHIBITED_FUNCTIONS
    prohibited_function_prefixes: tuple[str, ...] = DEFAULT_PROHIBITED_FUNCTION_PREFIXES
    allowed_sources: tuple[str, ...] = DEFAULT_ALLOWED_SOURCES
    reconciliation_only_sources: tuple[str, ...] = DEFAULT_RECONCILIATION_ONLY_SOURCES
    prohibit_raw_sql_from_consumers: bool = True
    prohibit_llm_generated_sql: bool = True
    require_date_filter: bool = False
    max_result_rows: int = 1000
    default_limit: int = 100
    max_dimensions: int = 5
    max_filters: int = 10
    max_filter_values: int = 100
    statement_timeout_seconds: int = 30
    idle_in_transaction_session_timeout_seconds: int = 60
    require_metric_allowlist: bool = True
    audiences: dict = field(default_factory=lambda: dict(DEFAULT_AUDIENCES))  # audience -> min_status
    unsupported_when: tuple[str, ...] = ()
    clarify_when: tuple[str, ...] = ()
    ambiguous_terms: tuple[AmbiguousTerm, ...] = ()
    unavailable_concepts: tuple[UnavailableConcept, ...] = ()
    max_metrics_per_query: int | None = None  # C-3.1: single source of the per-query metric limit
    glossary_terms: tuple[GlossaryTerm, ...] = ()


@dataclass(frozen=True)
class Release:
    name: str
    model: str
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...]


@dataclass(frozen=True)
class Catalog:
    root: Path
    connection: ConnectionDef
    sources: dict[str, Source]
    models: dict[str, SemanticModel]
    metrics: dict[str, Metric]
    relationships: dict[str, Relationship]
    prohibited_joins: tuple[ProhibitedJoin, ...]
    fact_to_fact_joins: str
    mappings: dict[str, CodeMapping]
    data_policy: DataPolicy
    query_policy: QueryPolicy
    releases: dict[str, Release]
    definitions_version: str

    def is_exposable(self, status: str, audience: str) -> bool:
        """``deprecated`` is never exposable; otherwise status >= audience minimum."""
        if status == "deprecated" or status not in STATUS_ORDER:
            return False
        minimum = self.query_policy.audiences.get(audience)
        if minimum is None or minimum not in STATUS_ORDER:
            return False
        return STATUS_ORDER[status] >= STATUS_ORDER[minimum]

    def model_for(self, metric: Metric) -> SemanticModel:
        model = self.models.get(metric.model or "")
        if model is None:
            raise DefinitionError("metrics", metric.name, f"unknown semantic model {metric.model!r}")
        return model
