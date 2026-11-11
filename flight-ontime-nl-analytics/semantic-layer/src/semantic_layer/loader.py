"""Definition loader (CONTRACT C-3) and cross-reference checks (C-1.4 R1-R17).

``load_catalog`` uses ``yaml.safe_load`` and validates structure strictly: unknown keys,
missing required keys, wrong container types, bad status enums and duplicate names raise
``DefinitionError(file, location, message)``. Semantic cross-checks (R1-R17) live in
``check_references`` and never raise for rule violations; they return offender strings.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

import yaml

from .errors import DefinitionError
from .models import (
    AmbiguousTerm, Approval, Catalog, ChangeLogEntry, CodeMapping, ConnectionDef,
    DataPolicy, Dimension, Entity, ExcludedColumn, Measure, MeasureFilter, Metric,
    ProhibitedJoin, QueryPolicy, Reconciliation, Relationship, Release, SemanticModel,
    GlossaryTerm, Source, UnavailableConcept,
)

STATUSES = ("draft", "validated", "approved", "deprecated")

F_SOURCES = "sources/postgres_sources.yml"
F_MODEL = "semantic_models/flight_operations.yml"
F_MAPPINGS = "semantic_models/code_mappings.yml"
F_METRICS = "metrics/flight_operations_metrics.yml"
F_RELS = "relationships/transport_relationships.yml"
F_DATA_POLICY = "policies/data_policy.yml"
F_QUERY_POLICY = "policies/query_policy.yml"
DEFINITION_FILES = (F_SOURCES, F_MODEL, F_MAPPINGS, F_METRICS, F_RELS, F_DATA_POLICY, F_QUERY_POLICY)


# --------------------------------------------------------------------------- helpers
class _V:
    """Validation helper bound to one file."""

    def __init__(self, file: str) -> None:
        self.file = file

    def fail(self, loc: str, msg: str) -> None:
        raise DefinitionError(self.file, loc, msg)

    def mapping(self, v: Any, loc: str, required: Iterable[str] = (), optional: Iterable[str] = ()) -> dict:
        if not isinstance(v, dict):
            self.fail(loc, "expected a mapping")
        allowed = set(required) | set(optional)
        for k in v:
            if not isinstance(k, str):
                self.fail(loc, f"non-string key {k!r}")
            if k not in allowed:
                self.fail(f"{loc}.{k}" if loc else k, f"unknown key '{k}'")
        for k in required:
            if k not in v:
                self.fail(loc, f"missing required key '{k}'")
        return v

    def seq(self, v: Any, loc: str) -> list:
        if not isinstance(v, list):
            self.fail(loc, "expected a list")
        return v

    def str_(self, v: Any, loc: str) -> str:
        if not isinstance(v, str):
            self.fail(loc, "expected a string")
        return v

    def opt_str(self, d: dict, k: str, loc: str) -> str | None:
        if k not in d or d[k] is None:
            return None
        return self.str_(d[k], f"{loc}.{k}")

    def int_(self, v: Any, loc: str) -> int:
        if isinstance(v, bool) or not isinstance(v, int):
            self.fail(loc, "expected an integer")
        return v

    def bool_(self, v: Any, loc: str) -> bool:
        if not isinstance(v, bool):
            self.fail(loc, "expected a boolean")
        return v

    def strs(self, v: Any, loc: str) -> tuple[str, ...]:
        return tuple(self.str_(x, f"{loc}[{i}]") for i, x in enumerate(self.seq(v, loc)))

    def enum(self, v: Any, loc: str, choices: Iterable[str]) -> str:
        s = self.str_(v, loc)
        choices = tuple(choices)
        if s not in choices:
            self.fail(loc, f"invalid value {s!r}; expected one of {', '.join(choices)}")
        return s

    def status(self, v: Any, loc: str) -> str:
        return self.enum(v, loc, STATUSES)

    def date_str(self, v: Any, loc: str) -> str | None:
        if v is None:
            return None
        if isinstance(v, (_dt.date, _dt.datetime)):
            return v.isoformat()
        return self.str_(v, loc)

    def unique(self, names: list[str], loc: str, kind: str) -> None:
        seen: set[str] = set()
        for n in names:
            if n in seen:
                self.fail(loc, f"duplicate {kind} name '{n}'")
            seen.add(n)


def _read_yaml(definitions_dir: Path, rel: str, top_required: Iterable[str], top_optional: Iterable[str] = ()) -> tuple[_V, dict]:
    v = _V(rel)
    path = definitions_dir / rel
    if not path.is_file():
        v.fail("", "definition file not found")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        v.fail("", f"invalid YAML: {str(exc).splitlines()[0] if str(exc) else 'parse error'}")
    except UnicodeDecodeError:
        v.fail("", "file is not valid UTF-8")
    data = v.mapping(data, "", ["version", *top_required], top_optional)
    if isinstance(data["version"], bool) or data["version"] != 1:
        v.fail("version", "expected version: 1")
    return v, data


def _filter(v: _V, raw: Any, loc: str) -> MeasureFilter:
    d = v.mapping(raw, loc, ["column", "operator", "value"])
    value = d["value"]
    if isinstance(value, list):
        value = tuple(value)
    return MeasureFilter(v.str_(d["column"], f"{loc}.column"), v.str_(d["operator"], f"{loc}.operator"), value)


# --------------------------------------------------------------------------- parsers
def _parse_sources(root: Path) -> tuple[ConnectionDef, dict[str, Source]]:
    v, data = _read_yaml(root, F_SOURCES, ["connection", "sources"])
    c = v.mapping(data["connection"], "connection",
                  ["engine", "database", "default_schema", "credentials", "read_only"])
    conn = ConnectionDef(
        v.str_(c["engine"], "connection.engine"), v.str_(c["database"], "connection.database"),
        v.str_(c["default_schema"], "connection.default_schema"),
        v.str_(c["credentials"], "connection.credentials"), v.bool_(c["read_only"], "connection.read_only"))
    req = ["name", "physical_object", "object_type", "usage", "grain", "classification", "status", "freshness"]
    opt = ["primary_key", "business_owner", "technical_owner", "time_column", "description"]
    out: dict[str, Source] = {}
    names = []
    for i, raw in enumerate(v.seq(data["sources"], "sources")):
        loc = f"sources[{i}]"
        d = v.mapping(raw, loc, req, opt)
        otype = v.enum(d["object_type"], f"{loc}.object_type", ("table", "view"))
        if otype == "table" and "primary_key" not in d:
            v.fail(loc, "missing required key 'primary_key' (object_type=table)")
        s = Source(
            name=v.str_(d["name"], f"{loc}.name"), physical_object=v.str_(d["physical_object"], f"{loc}.physical_object"),
            object_type=otype, usage=v.enum(d["usage"], f"{loc}.usage", ("queryable", "reconciliation_only")),
            grain=v.str_(d["grain"], f"{loc}.grain"), classification=v.str_(d["classification"], f"{loc}.classification"),
            status=v.status(d["status"], f"{loc}.status"), freshness=v.str_(d["freshness"], f"{loc}.freshness"),
            primary_key=v.strs(d.get("primary_key", []), f"{loc}.primary_key"),
            business_owner=v.opt_str(d, "business_owner", loc), technical_owner=v.opt_str(d, "technical_owner", loc),
            time_column=v.opt_str(d, "time_column", loc), description=v.opt_str(d, "description", loc))
        names.append(s.name)
        out[s.name] = s
    v.unique(names, "sources", "source")
    return conn, out


def _parse_models(root: Path) -> dict[str, SemanticModel]:
    v, data = _read_yaml(root, F_MODEL, ["semantic_models"])
    out: dict[str, SemanticModel] = {}
    names = []
    for i, raw in enumerate(v.seq(data["semantic_models"], "semantic_models")):
        loc = f"semantic_models[{i}]"
        d = v.mapping(raw, loc, ["name", "status", "primary_source", "grain", "dimensions", "measures"],
                      ["label", "time_dimension", "entities", "excluded_columns"])
        name = v.str_(d["name"], f"{loc}.name")
        status = v.status(d["status"], f"{loc}.status")
        primary = v.str_(d["primary_source"], f"{loc}.primary_source")
        # entities
        entities, enames = [], []
        for j, e in enumerate(v.seq(d.get("entities", []), f"{loc}.entities")):
            el = f"{loc}.entities[{j}]"
            e = v.mapping(e, el, ["name", "type", "column"], ["references"])
            entities.append(Entity(v.str_(e["name"], f"{el}.name"), v.enum(e["type"], f"{el}.type", ("primary", "foreign")),
                                   v.str_(e["column"], f"{el}.column"), v.opt_str(e, "references", el)))
            enames.append(entities[-1].name)
        v.unique(enames, f"{loc}.entities", "entity")
        # dimensions
        dims, dnames = [], []
        for j, e in enumerate(v.seq(d["dimensions"], f"{loc}.dimensions")):
            dl = f"{loc}.dimensions[{j}]"
            e = v.mapping(e, dl, ["name", "label", "type", "data_type", "column"],
                          ["source", "role", "granularities", "values", "null_policy", "status", "mapping", "note"])
            values = e.get("values")
            if isinstance(values, list):
                values = tuple(values)
            elif values is not None and not isinstance(values, dict):
                v.fail(f"{dl}.values", "expected a mapping or a list")
            dtype = v.enum(e["type"], f"{dl}.type", ("time", "categorical", "ordinal"))
            gran = v.strs(e.get("granularities", []), f"{dl}.granularities")
            if gran and dtype != "time":
                v.fail(f"{dl}.granularities", "granularities are allowed on time dimensions only")
            dims.append(Dimension(
                name=v.str_(e["name"], f"{dl}.name"), label=v.str_(e["label"], f"{dl}.label"), type=dtype,
                data_type=v.enum(e["data_type"], f"{dl}.data_type", ("date", "integer", "text")),
                column=v.str_(e["column"], f"{dl}.column"),
                source=v.opt_str(e, "source", dl) or primary, role=v.opt_str(e, "role", dl), granularities=gran,
                values=values,
                null_policy=v.enum(e.get("null_policy", "none"), f"{dl}.null_policy",
                                   ("none", "label_with_city_name", "not_cancelled")),
                status=v.status(e["status"], f"{dl}.status") if "status" in e else status,
                mapping=v.opt_str(e, "mapping", dl), note=v.opt_str(e, "note", dl)))
            dnames.append(dims[-1].name)
        v.unique(dnames, f"{loc}.dimensions", "dimension")
        # measures
        measures, mnames = [], []
        for j, e in enumerate(v.seq(d["measures"], f"{loc}.measures")):
            ml = f"{loc}.measures[{j}]"
            e = v.mapping(e, ml, ["name", "aggregation", "expression"], ["filter"])
            flt = _filter(v, e["filter"], f"{ml}.filter") if e.get("filter") is not None else None
            measures.append(Measure(v.str_(e["name"], f"{ml}.name"), v.str_(e["aggregation"], f"{ml}.aggregation"),
                                    v.str_(e["expression"], f"{ml}.expression"), flt))
            mnames.append(measures[-1].name)
        v.unique(mnames, f"{loc}.measures", "measure")
        # excluded columns
        excluded = []
        for j, e in enumerate(v.seq(d.get("excluded_columns", []), f"{loc}.excluded_columns")):
            xl = f"{loc}.excluded_columns[{j}]"
            e = v.mapping(e, xl, ["reason"], ["source", "column", "columns"])
            if ("column" in e) == ("columns" in e):
                v.fail(xl, "exactly one of 'column' or 'columns' is required")
            cols = (v.str_(e["column"], f"{xl}.column"),) if "column" in e else v.strs(e["columns"], f"{xl}.columns")
            excluded.append(ExcludedColumn(v.opt_str(e, "source", xl) or primary, cols, v.str_(e["reason"], f"{xl}.reason")))
        names.append(name)
        out[name] = SemanticModel(
            name=name, label=v.opt_str(d, "label", loc) or name, status=status, primary_source=primary,
            grain=v.str_(d["grain"], f"{loc}.grain"), time_dimension=v.opt_str(d, "time_dimension", loc),
            entities=tuple(entities), dimensions=tuple(dims), measures=tuple(measures), excluded_columns=tuple(excluded))
    v.unique(names, "semantic_models", "semantic model")
    return out


def _parse_mappings(root: Path) -> dict[str, CodeMapping]:
    v, data = _read_yaml(root, F_MAPPINGS, ["mappings"])
    out: dict[str, CodeMapping] = {}
    names = []
    for i, raw in enumerate(v.seq(data["mappings"], "mappings")):
        loc = f"mappings[{i}]"
        d = v.mapping(raw, loc, ["name", "status", "keys_from", "values"], ["unmapped_policy"])
        if not isinstance(d["values"], dict):
            v.fail(f"{loc}.values", "expected a mapping")
        m = CodeMapping(v.str_(d["name"], f"{loc}.name"), v.status(d["status"], f"{loc}.status"),
                        v.str_(d["keys_from"], f"{loc}.keys_from"), dict(d["values"]),
                        v.str_(d.get("unmapped_policy", "show_code"), f"{loc}.unmapped_policy"))
        names.append(m.name)
        out[m.name] = m
    v.unique(names, "mappings", "mapping")
    return out


def _parse_metrics(root: Path) -> tuple[dict[str, Metric], dict[str, Release]]:
    v, data = _read_yaml(root, F_METRICS, ["metrics"], ["releases"])
    keys = ["label", "description", "status", "version", "business_owner", "technical_owner", "model", "source",
            "grain", "type", "measure", "numerator", "denominator", "expression", "filters", "null_handling",
            "allowed_dimensions", "unit", "time_basis", "validation_sql", "reconciliation", "approval",
            "open_question", "caveats", "change_log", "combinable_with"]
    metrics: dict[str, Metric] = {}
    names = []
    for i, raw in enumerate(v.seq(data["metrics"], "metrics")):
        loc = f"metrics[{i}]"
        d = v.mapping(raw, loc, ["name"], keys)
        s = lambda k: v.opt_str(d, k, loc)  # noqa: E731
        rec = None
        if d.get("reconciliation") is not None:
            r = v.mapping(d["reconciliation"], f"{loc}.reconciliation",
                          optional=["baseline", "scope", "as_of", "tolerance", "reference"])
            rec = Reconciliation(r.get("baseline"), v.opt_str(r, "scope", f"{loc}.reconciliation"),
                                 v.date_str(r.get("as_of"), f"{loc}.reconciliation.as_of"), r.get("tolerance"),
                                 v.opt_str(r, "reference", f"{loc}.reconciliation"))
        appr = None
        if d.get("approval") is not None:
            a = v.mapping(d["approval"], f"{loc}.approval", optional=["status", "approver", "approved_on"])
            appr = Approval(v.opt_str(a, "status", f"{loc}.approval"), v.opt_str(a, "approver", f"{loc}.approval"),
                            v.date_str(a.get("approved_on"), f"{loc}.approval.approved_on"))
        filters = None
        if "filters" in d:
            filters = tuple(_filter(v, f, f"{loc}.filters[{j}]") for j, f in enumerate(v.seq(d["filters"] or [], f"{loc}.filters")))
        log = None
        if d.get("change_log") is not None:
            log = []
            for j, e in enumerate(v.seq(d["change_log"], f"{loc}.change_log")):
                el = f"{loc}.change_log[{j}]"
                e = v.mapping(e, el, ["version", "date", "change", "definition_hash"])
                log.append(ChangeLogEntry(v.int_(e["version"], f"{el}.version"), v.date_str(e["date"], f"{el}.date") or "",
                                          v.str_(e["change"], f"{el}.change"), v.str_(e["definition_hash"], f"{el}.definition_hash")))
            log = tuple(log)
        mtype = s("type")
        if mtype is not None:
            v.enum(mtype, f"{loc}.type", ("simple", "ratio"))
        unit = s("unit")
        if unit is not None:
            v.enum(unit, f"{loc}.unit", ("count", "ratio", "percent"))
        status = s("status")
        if status is not None:
            v.status(status, f"{loc}.status")
        oq = d.get("open_question")
        m = Metric(
            name=v.str_(d["name"], f"{loc}.name"), label=s("label"), description=s("description"), status=status,
            version=v.int_(d["version"], f"{loc}.version") if d.get("version") is not None else None,
            business_owner=s("business_owner"), technical_owner=s("technical_owner"), model=s("model"),
            source=s("source"), grain=s("grain"), type=mtype, measure=s("measure"), numerator=s("numerator"),
            denominator=s("denominator"), expression=s("expression"), filters=filters, null_handling=s("null_handling"),
            allowed_dimensions=v.strs(d["allowed_dimensions"], f"{loc}.allowed_dimensions") if d.get("allowed_dimensions") is not None else None,
            unit=unit, time_basis=s("time_basis"), validation_sql=s("validation_sql"), reconciliation=rec, approval=appr,
            open_question=str(oq) if oq is not None else None, caveats=v.strs(d.get("caveats") or [], f"{loc}.caveats"),
            change_log=log, combinable_with=s("combinable_with"))
        names.append(m.name)
        metrics[m.name] = m
    v.unique(names, "metrics", "metric")
    releases: dict[str, Release] = {}
    rnames = []
    for i, raw in enumerate(v.seq(data.get("releases", []), "releases")):
        loc = f"releases[{i}]"
        d = v.mapping(raw, loc, ["name", "model", "metrics", "dimensions"])
        r = Release(v.str_(d["name"], f"{loc}.name"), v.str_(d["model"], f"{loc}.model"),
                    v.strs(d["metrics"], f"{loc}.metrics"), v.strs(d["dimensions"], f"{loc}.dimensions"))
        rnames.append(r.name)
        releases[r.name] = r
    v.unique(rnames, "releases", "release")
    groups: dict[str, tuple[str | None, str | None, str | None]] = {}
    for m in metrics.values():  # C-3.2 load-time invariant
        if m.combinable_with is None:
            continue
        shape = (m.model, m.grain, m.time_basis)
        if groups.setdefault(m.combinable_with, shape) != shape:
            raise DefinitionError(F_METRICS, "metrics",
                                  f"{m.name}: combinable_with group '{m.combinable_with}' mixes model/grain/time_basis")
    return metrics, releases


def _parse_relationships(root: Path) -> tuple[dict[str, Relationship], tuple[ProhibitedJoin, ...], str]:
    v, data = _read_yaml(root, F_RELS, ["relationships"], ["prohibited_joins", "fact_to_fact_joins"])
    rels: dict[str, Relationship] = {}
    names = []
    for i, raw in enumerate(v.seq(data["relationships"], "relationships")):
        loc = f"relationships[{i}]"
        d = v.mapping(raw, loc, ["name", "left", "right", "type", "join", "status"], ["role", "enforced_fk", "verified"])
        ver = None
        if d.get("verified") is not None:
            ver = v.mapping(d["verified"], f"{loc}.verified", optional=["row_loss", "duplication", "as_of", "check"])
            ver = dict(ver)
            ver["as_of"] = v.date_str(ver.get("as_of"), f"{loc}.verified.as_of")
        r = Relationship(
            name=v.str_(d["name"], f"{loc}.name"), left=v.str_(d["left"], f"{loc}.left"), right=v.str_(d["right"], f"{loc}.right"),
            type=v.str_(d["type"], f"{loc}.type"), join=v.str_(d["join"], f"{loc}.join"),
            status=v.status(d["status"], f"{loc}.status"), role=v.opt_str(d, "role", loc),
            enforced_fk=v.bool_(d.get("enforced_fk", False), f"{loc}.enforced_fk"), verified=ver)
        names.append(r.name)
        rels[r.name] = r
    v.unique(names, "relationships", "relationship")
    pjs = []
    for i, raw in enumerate(v.seq(data.get("prohibited_joins", []), "prohibited_joins")):
        loc = f"prohibited_joins[{i}]"
        d = v.mapping(raw, loc, ["source", "column", "reason"])
        pjs.append(ProhibitedJoin(v.str_(d["source"], f"{loc}.source"), v.str_(d["column"], f"{loc}.column"),
                                  v.str_(d["reason"], f"{loc}.reason")))
    ftf = data.get("fact_to_fact_joins")
    return rels, tuple(pjs), (v.str_(ftf, "fact_to_fact_joins") if ftf is not None else "")


def _parse_data_policy(root: Path) -> DataPolicy:
    v, data = _read_yaml(root, F_DATA_POLICY, ["default", "classification_defaults", "prohibited_column_patterns"], ["pii"])
    if not isinstance(data["classification_defaults"], dict):
        v.fail("classification_defaults", "expected a mapping")
    return DataPolicy(v.str_(data["default"], "default"), v.str_(data.get("pii", "none"), "pii"),
                      {str(k): str(x) for k, x in data["classification_defaults"].items()},
                      v.strs(data["prohibited_column_patterns"], "prohibited_column_patterns"))


def _parse_query_policy(root: Path) -> QueryPolicy:
    list_keys = ["allowed_statement_types", "prohibited_statement_types", "prohibited_keywords", "prohibited_functions",
                 "prohibited_function_prefixes", "allowed_sources", "reconciliation_only_sources", "unsupported_when",
                 "clarify_when"]
    int_keys = ["max_result_rows", "default_limit", "max_dimensions", "max_filters", "max_filter_values",
                "statement_timeout_seconds", "idle_in_transaction_session_timeout_seconds"]
    bool_keys = ["prohibit_raw_sql_from_consumers", "prohibit_llm_generated_sql", "require_date_filter",
                 "require_metric_allowlist"]
    v, data = _read_yaml(root, F_QUERY_POLICY, [],
                         ["database_access", "audiences", "ambiguous_terms", "unavailable_concepts",
                          "max_metrics_per_query", "glossary_terms",
                          *list_keys, *int_keys, *bool_keys])
    kw: dict[str, Any] = {}
    if "database_access" in data:
        kw["database_access"] = v.str_(data["database_access"], "database_access")
    for k in list_keys:
        if k in data:
            kw[k] = v.strs(data[k], k)
    for k in int_keys:
        if k in data:
            kw[k] = v.int_(data[k], k)
    for k in bool_keys:
        if k in data:
            kw[k] = v.bool_(data[k], k)
    if "audiences" in data:
        aud = v.mapping(data["audiences"], "audiences", optional=list(data["audiences"]) if isinstance(data["audiences"], dict) else ())
        kw["audiences"] = {}
        for name, val in aud.items():
            e = v.mapping(val, f"audiences.{name}", ["min_status"])
            kw["audiences"][name] = v.status(e["min_status"], f"audiences.{name}.min_status")
    kw["ambiguous_terms"] = tuple(
        AmbiguousTerm(v.str_(e["reason_code"], f"ambiguous_terms[{i}].reason_code"),
                      v.strs(e["terms"], f"ambiguous_terms[{i}].terms"), v.strs(e.get("options", []), f"ambiguous_terms[{i}].options"),
                      v.str_(e["question"], f"ambiguous_terms[{i}].question") if e.get("question") is not None else None)
        for i, e in enumerate(v.mapping(x, f"ambiguous_terms[{i}]", ["reason_code", "terms"], ["options", "question"])
                              for i, x in enumerate(v.seq(data.get("ambiguous_terms", []), "ambiguous_terms"))))
    kw["unavailable_concepts"] = tuple(
        UnavailableConcept(v.str_(e["reason_code"], f"unavailable_concepts[{i}].reason_code"),
                           v.str_(e["reason"], f"unavailable_concepts[{i}].reason"),
                           v.strs(e["terms"], f"unavailable_concepts[{i}].terms"))
        for i, e in enumerate(v.mapping(x, f"unavailable_concepts[{i}]", ["reason_code", "reason", "terms"])
                              for i, x in enumerate(v.seq(data.get("unavailable_concepts", []), "unavailable_concepts"))))
    if "max_metrics_per_query" not in data:
        raise DefinitionError(F_QUERY_POLICY, "max_metrics_per_query", "required key is missing")
    mmq = v.int_(data["max_metrics_per_query"], "max_metrics_per_query")
    if mmq < 1:
        raise DefinitionError(F_QUERY_POLICY, "max_metrics_per_query", "expected an integer >= 1")
    kw["max_metrics_per_query"] = mmq
    kw["glossary_terms"] = tuple(
        GlossaryTerm(v.str_(e["term"], f"glossary_terms[{i}].term"), v.str_(e["meaning"], f"glossary_terms[{i}].meaning"),
                     v.strs(e["maps_to"], f"glossary_terms[{i}].maps_to"))
        for i, e in enumerate(v.mapping(x, f"glossary_terms[{i}]", ["term", "meaning", "maps_to"])
                              for i, x in enumerate(v.seq(data.get("glossary_terms", []), "glossary_terms"))))
    return QueryPolicy(**kw)


def _definitions_version(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted((q for q in root.rglob("*.yml") if q.is_file()), key=lambda q: q.relative_to(root).as_posix()):
        h.update(p.relative_to(root).as_posix().encode("utf-8") + b"\n" + p.read_bytes() + b"\n")
    return h.hexdigest()[:12]


def load_catalog(definitions_dir: str | Path) -> Catalog:
    """Load and strictly validate the seven definition files. Raises DefinitionError."""
    root = Path(definitions_dir)
    if not root.is_dir():
        raise DefinitionError(str(root), "", "definitions directory not found")
    root = root.resolve()
    connection, sources = _parse_sources(root)
    models = _parse_models(root)
    mappings = _parse_mappings(root)
    metrics, releases = _parse_metrics(root)
    relationships, prohibited, ftf = _parse_relationships(root)
    query_policy = _parse_query_policy(root)
    known = set(metrics) | {d.name for mo in models.values() for d in mo.dimensions}
    for i, g in enumerate(query_policy.glossary_terms):  # C-3.1: maps_to must name catalog objects
        for t in g.maps_to:
            if t not in known:
                raise DefinitionError(F_QUERY_POLICY, f"glossary_terms[{i}].maps_to",
                                      f"{t!r} is not a catalog metric or dimension")
    return Catalog(
        root=root, connection=connection, sources=sources, models=models, metrics=metrics,
        relationships=relationships, prohibited_joins=prohibited, fact_to_fact_joins=ftf, mappings=mappings,
        data_policy=_parse_data_policy(root), query_policy=query_policy, releases=releases,
        definitions_version=_definitions_version(root))


# --------------------------------------------------------------------------- hash (C-3.4)
def _measure_payload(model: SemanticModel, name: str | None) -> dict | None:
    if name is None:
        return None
    m = model.measure(name)
    if m is None:
        return None
    return {"aggregation": m.aggregation, "expression": m.expression, "filter": _flt_payload(m.filter)}


def _flt_payload(f: MeasureFilter | None) -> dict | None:
    if f is None:
        return None
    value = list(f.value) if isinstance(f.value, tuple) else f.value
    return {"column": f.column, "operator": f.operator, "value": value}


def metric_definition_hash(metric: Metric, model: SemanticModel) -> str:
    payload = {
        "name": metric.name, "type": metric.type, "time_basis": metric.time_basis, "null_handling": metric.null_handling,
        "filters": [_flt_payload(f) for f in (metric.filters or ())],
        "measure": _measure_payload(model, metric.measure),
        "numerator": _measure_payload(model, metric.numerator),
        "denominator": _measure_payload(model, metric.denominator),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(blob).hexdigest()[:16]


# --------------------------------------------------------------------------- R1-R17 (C-1.4)
_FLAGS = ("dep_del15", "arr_del15")
_PLACEHOLDER = re.compile(r"^<.*>$")


def _split_ref(ref: str) -> tuple[str, str]:
    src, _, col = ref.partition(".")
    return src, col


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _sql_checks(sql_dir: Path, ref: str) -> str | None:
    """Return an error message if '<file>#<check>[.<column>]' does not resolve, else None."""
    file_part, _, check_part = ref.partition("#")
    check = check_part.split(".", 1)[0]
    candidates = [sql_dir / Path(file_part).name, sql_dir.parent / file_part]
    path = next((c for c in candidates if c.is_file()), None)
    if path is None or not check:
        return f"{ref}: file or check name not found"
    names = set(re.findall(r"^\s*--\s*@check:\s*(\S+)", path.read_text(encoding="utf-8"), flags=re.M))
    return None if check in names else f"{ref}: check '{check}' not found in {path.name}"


def check_references(catalog: Catalog, sql_dir: str | Path | None = None) -> list[str]:
    """Cross-reference rules R1-R17; each offender is ``R<n>:<object>:<message>``."""
    bad: list[str] = []
    add = lambda n, obj, msg: bad.append(f"R{n}:{obj}:{msg}")  # noqa: E731
    cat = catalog
    primaries = {m.primary_source for m in cat.models.values()}

    for s in cat.sources.values():  # R1
        if not re.fullmatch(r"ontime\.[a-z_]+", s.physical_object):
            add(1, s.name, f"physical_object {s.physical_object!r} must match ^ontime\\.[a-z_]+$")

    for model in cat.models.values():
        mn = model.name
        src = cat.sources.get(model.primary_source)  # R2
        if src is None:
            add(2, mn, f"primary_source {model.primary_source!r} does not exist")
        elif src.usage != "queryable":
            add(2, mn, f"primary_source {model.primary_source!r} is not queryable")
        for e in model.entities:  # R3
            if e.references and _split_ref(e.references)[0] not in cat.sources:
                add(3, f"{mn}.{e.name}", f"references unknown source in {e.references!r}")
        for d in model.dimensions:  # R4, R5, R16
            obj = f"{mn}.{d.name}"
            if d.source not in cat.sources:
                add(4, obj, f"source {d.source!r} does not exist")
            else:
                if d.source == "airports":
                    if d.role not in ("origin_airport", "destination_airport"):
                        add(4, obj, "role must be origin_airport or destination_airport for airports")
                elif d.role is not None:
                    add(4, obj, "role is only allowed for airports dimensions")
                if d.source != model.primary_source and not any(
                    _split_ref(r.right)[0] == d.source and r.role == d.role for r in cat.relationships.values()
                ):
                    add(4, obj, f"no relationship to source {d.source!r} with role {d.role!r}")
            if d.mapping is not None and d.mapping not in cat.mappings:
                add(5, obj, f"mapping {d.mapping!r} does not exist")
            if d.column == "city_market_id":
                add(16, obj, "city_market_id must not be used")
        for e in model.entities:
            if e.column == "city_market_id":
                add(16, f"{mn}.{e.name}", "city_market_id must not be used")
        for m in model.measures:  # R6, R16
            obj = f"{mn}.{m.name}"
            if m.aggregation not in ("count", "sum"):
                add(6, obj, f"aggregation {m.aggregation!r} must be count or sum")
            if m.aggregation == "sum" and m.expression == "*":
                add(6, obj, "sum requires a column, not '*'")
            if m.filter is not None:
                v = m.filter.value
                if not (_is_int(v) or (isinstance(v, tuple) and v and all(_is_int(x) for x in v))):
                    add(6, obj, "filter value must be an int or a list of ints")
            if "city_market_id" in (m.expression, m.filter.column if m.filter else None):
                add(16, obj, "city_market_id must not be used")

    for r in cat.relationships.values():  # R10, R11, R16
        lsrc, lcol = _split_ref(r.left)
        rsrc, rcol = _split_ref(r.right)
        if lsrc not in primaries:
            add(10, r.name, f"left source {lsrc!r} is not a primary source")
        rs = cat.sources.get(rsrc)
        if rs is None or rcol not in rs.primary_key:
            add(10, r.name, f"right {r.right!r} must be <source>.<primary key column>")
        if r.type != "many_to_one" or r.join != "inner":
            add(10, r.name, "type must be many_to_one and join must be inner")
        if any(pj.source == rsrc and pj.column == rcol for pj in cat.prohibited_joins):
            add(11, r.name, f"right side {r.right!r} is a prohibited join")
        if "city_market_id" in (lcol, rcol):
            add(16, r.name, "city_market_id must not be used")
    if cat.fact_to_fact_joins != "prohibited":
        add(11, "fact_to_fact_joins", "must be 'prohibited'")

    for met in cat.metrics.values():  # R7, R8, R9
        model = cat.models.get(met.model or "")
        if model is None:
            add(7, met.name, f"model {met.model!r} does not exist")
            continue
        if met.source != model.primary_source:
            add(7, met.name, f"source {met.source!r} must equal model primary source {model.primary_source!r}")
        refs: list[str | None] = []
        if met.type == "simple":
            if not met.measure or met.numerator or met.denominator:
                add(7, met.name, "simple metric needs measure and no numerator/denominator")
            refs = [met.measure]
        elif met.type == "ratio":
            if not (met.numerator and met.denominator) or met.measure:
                add(7, met.name, "ratio metric needs numerator and denominator and no measure")
            refs = [met.numerator, met.denominator]
        else:
            add(7, met.name, f"type {met.type!r} must be simple or ratio")
        for ref in refs:
            if ref and model.measure(ref) is None:
                add(7, met.name, f"measure {ref!r} does not exist")
        for dn in met.allowed_dimensions or ():
            if model.dimension(dn) is None:
                add(8, met.name, f"allowed dimension {dn!r} does not exist in model")
        if met.type == "ratio" and met.numerator and met.denominator:
            num, den = model.measure(met.numerator), model.measure(met.denominator)
            if num is not None and den is not None:
                flags = {c for c in (num.expression, num.filter.column if num.filter else None) if c in _FLAGS}
                if flags and not (den.aggregation == "count" and den.expression in flags and den.filter is None):
                    add(9, met.name, f"denominator must be COUNT({'/'.join(sorted(flags))}) with no filter")
        for f in met.filters or ():
            if f.column == "city_market_id":
                add(16, met.name, "city_market_id must not be used")

    qp = cat.query_policy  # R12
    for label, actual, usage in (("allowed_sources", qp.allowed_sources, "queryable"),
                                 ("reconciliation_only_sources", qp.reconciliation_only_sources, "reconciliation_only")):
        expected = {s.physical_object for s in cat.sources.values() if s.usage == usage}
        if set(actual) != expected:
            add(12, label, f"must equal {sorted(expected)}; found {sorted(actual)}")

    for rel in cat.releases.values():  # R13
        model = cat.models.get(rel.model)
        if model is None:
            add(13, rel.name, f"model {rel.model!r} does not exist")
            continue
        for mn in rel.metrics:
            m = cat.metrics.get(mn)
            if m is None:
                add(13, rel.name, f"metric {mn!r} does not exist")
            elif m.status not in ("validated", "approved"):
                add(13, rel.name, f"metric {mn!r} has status {m.status!r}")
        for dn in rel.dimensions:
            d = model.dimension(dn)
            if d is None:
                add(13, rel.name, f"dimension {dn!r} does not exist")
            elif d.status not in ("validated", "approved"):
                add(13, rel.name, f"dimension {dn!r} has status {d.status!r}")

    names = {m for m in cat.metrics} | {d.name for mo in cat.models.values() for d in mo.dimensions}
    for term_owner in (*qp.ambiguous_terms, *qp.unavailable_concepts):  # R14
        for t in term_owner.terms:
            if t in names:
                add(14, term_owner.reason_code, f"term {t!r} equals a catalog metric or dimension name")

    approved_objs: list[tuple[str, str]] = []  # R15
    approved_objs += [("source " + s.name, s.name) for s in cat.sources.values() if s.status == "approved"]
    for mo in cat.models.values():
        if mo.status == "approved":
            approved_objs.append((f"model {mo.name}", mo.name))
        approved_objs += [(f"dimension {d.name}", f"{mo.name}.{d.name}") for d in mo.dimensions if d.status == "approved"]
    approved_objs += [(f"relationship {r.name}", r.name) for r in cat.relationships.values() if r.status == "approved"]
    approved_objs += [(f"mapping {m.name}", m.name) for m in cat.mappings.values() if m.status == "approved"]
    for label, obj in approved_objs:
        add(15, obj, f"{label} is approved but has no approval block and reconciliation")
    for met in cat.metrics.values():
        if met.status != "approved":
            continue
        a = met.approval
        if a is None or a.status != "approved":
            add(15, met.name, "approval.status must be approved")
        elif not a.approver or _PLACEHOLDER.match(a.approver):
            add(15, met.name, "approval.approver must be a non-placeholder name")
        elif not a.approved_on:
            add(15, met.name, "approval.approved_on is required")
        if met.reconciliation is None:
            add(15, met.name, "reconciliation block is required")

    if sql_dir is not None:  # R17
        sd = Path(sql_dir)
        for met in cat.metrics.values():
            for ref in (met.validation_sql, met.reconciliation.reference if met.reconciliation else None):
                if ref:
                    problem = _sql_checks(sd, ref)
                    if problem:
                        add(17, met.name, problem)
    return bad
