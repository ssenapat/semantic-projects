"""Loads and statically validates the YAML definitions (spec sections 8, 9)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import DefinitionError

STATES = {"draft", "validated", "approved", "deprecated"}
EXPOSED_STATES = {"validated", "approved"}
MODEL_REQUIRED = ["name", "label", "description", "version", "status", "business_owner", "technical_owner",
                  "source", "grain", "null_handling", "change_log", "base", "joins", "dimensions", "measures"]
MEASURE_REQUIRED = ["name", "label", "description", "unit", "sql", "requires", "status", "version",
                    "validation", "change_log", "combinable_with", "allowed_dimensions"]
DIMENSION_REQUIRED = ["name", "label", "description", "type", "expression", "requires"]
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
ALIAS_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DATA_TYPES = ("string", "integer", "number", "boolean", "date", "timestamp")
POLICY_LIMITS = ("max_measures_per_query", "max_dimensions_per_query", "default_limit", "max_rows",
                 "max_joins", "max_fan_out_dimensions")
POLICY_PATH = ("policy", "query_policy.yml")


@dataclass(frozen=True)
class Policy:
    """Resource limits read from definitions/policy/query_policy.yml (single source)."""
    max_measures_per_query: int
    max_dimensions_per_query: int
    default_limit: int
    max_rows: int
    max_joins: int
    max_fan_out_dimensions: int
    version: str

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in (*POLICY_LIMITS, "version")}


def _load_policy(defs_dir: Path) -> Policy:
    path = defs_dir.joinpath(*POLICY_PATH)
    if not path.is_file():
        raise DefinitionError(f"query policy file not found: {path}")
    raw = _read(path)
    if not isinstance(raw, dict):
        raise DefinitionError(f"{path.name}: must be a mapping")
    for k in POLICY_LIMITS:
        v = raw.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise DefinitionError(f"{path.name}: {k} must be an integer >= 1, got {v!r}")
    if raw["default_limit"] > raw["max_rows"]:
        raise DefinitionError(f"{path.name}: default_limit {raw['default_limit']} exceeds max_rows {raw['max_rows']}")
    version = raw.get("version")
    if not isinstance(version, str) or not version:
        raise DefinitionError(f"{path.name}: version is required")
    return Policy(version=version, **{k: raw[k] for k in POLICY_LIMITS})


def _parse_allowed(where: str, m: dict, dims: dict[str, dict]) -> dict[str, "frozenset[str] | None"]:
    """Validate allowed_dimensions and return {dimension: grains or None for all grains}."""
    tag = f"{where} measure {m['name']}"
    if not isinstance(m["combinable_with"], str) or not NAME_RE.match(m["combinable_with"]):
        raise DefinitionError(f"{tag}: combinable_with {m['combinable_with']!r} is not a valid name")
    entries = m["allowed_dimensions"]
    if not isinstance(entries, list) or not entries or not all(isinstance(e, str) for e in entries):
        raise DefinitionError(f"{tag}: allowed_dimensions must be a non-empty list of strings")
    if len(set(entries)) != len(entries):
        raise DefinitionError(f"{tag}: allowed_dimensions has duplicates")
    parsed: dict[str, set[str] | None] = {}
    for e in entries:
        dim, sep, grain = e.partition(":")
        if dim not in dims:
            raise DefinitionError(f"{tag}: allowed_dimensions names unknown dimension {dim!r}")
        if sep:
            d = dims[dim]
            if d["type"] != "time":
                raise DefinitionError(f"{tag}: grain {grain!r} on non-time dimension {dim!r}")
            if grain not in d.get("granularities", []):
                raise DefinitionError(f"{tag}: grain {grain!r} is not a granularity of dimension {dim!r}")
            if parsed.get(dim, set()) is None:
                raise DefinitionError(f"{tag}: allowed_dimensions mixes {dim!r} and {dim + ':' + grain!r}")
            parsed.setdefault(dim, set()).add(grain)
        else:
            if dim in parsed:
                raise DefinitionError(f"{tag}: allowed_dimensions mixes {dim!r} and a grain of it")
            parsed[dim] = None
    return {k: (None if v is None else frozenset(v)) for k, v in parsed.items()}


@dataclass
class Model:
    raw: dict
    name: str
    joins: dict[str, dict]            # alias -> join definition (declaration order preserved)
    dimensions: dict[str, dict]
    measures: dict[str, dict]

    @property
    def version(self) -> str:
        return self.raw["version"]


@dataclass
class Definitions:
    sources: dict[str, dict]
    derived: dict[str, dict]
    reference: dict[str, dict]
    relationships: dict[str, dict]
    blocked_columns: set[str]
    models: dict[str, Model]
    root: Path
    allow_draft: bool = False
    check_ids: set[str] = field(default_factory=set)
    policy: Policy | None = None

    def table_allow_list(self, include_reference: bool = False) -> set[str]:
        allowed = {s["physical_object"].split(".")[-1] for s in self.sources.values()}
        if include_reference:
            allowed |= {s["physical_object"].split(".")[-1] for s in self.reference.values()}
        return allowed

    def is_exposed(self, obj: dict, model: dict | None = None) -> bool:
        if obj.get("expose") is False:
            return False
        states = EXPOSED_STATES | ({"draft"} if self.allow_draft else set())
        status = obj.get("status") or (model["status"] if model else "draft")
        return status in states and (model is None or model["status"] in states)


def _read(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise DefinitionError(f"{path}: invalid YAML: {exc}") from exc


def _require(obj: dict, keys: list[str], where: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise DefinitionError(f"{where}: missing required fields {missing}")


def find_check_ids(validation_dir: Path) -> set[str]:
    ids = {p.stem for p in validation_dir.glob("*.sql")}
    return ids | {"X_allocated_rentals_sum", "X_revenue_by_rental_month_sums", "X_allocated_revenue_sum"}


def load_definitions(root: str | Path, allow_draft: bool = False) -> Definitions:
    root = Path(root)
    defs_dir = root / "definitions"
    if not defs_dir.is_dir():
        raise DefinitionError(f"definitions directory not found: {defs_dir}")

    src_doc = _read(defs_dir / "sources" / "pagila_sources.yml")
    sources = {s["name"]: s for s in src_doc.get("sources", [])}
    derived = {s["name"]: s for s in src_doc.get("derived_sources", [])}
    reference = {s["name"]: s for s in src_doc.get("reference_only", [])}
    blocked = {c.lower() for c in src_doc.get("blocked_columns", [])}

    rel_doc = _read(defs_dir / "relationships" / "pagila_relationships.yml")
    relationships = {r["name"]: r for r in rel_doc.get("relationships", [])}
    for rel in relationships.values():
        for end in (rel["from"], rel["to"]):
            table = end.split(".")[0]
            if table not in sources:
                raise DefinitionError(f"relationship {rel['name']}: unknown source table {table!r}")

    check_ids = find_check_ids(root / "validation")
    table_names = {s["physical_object"] for s in sources.values()}

    policy = _load_policy(defs_dir)
    models: dict[str, Model] = {}
    for path in sorted((defs_dir / "semantic_models").glob("*.yml")):
        raw = _read(path)
        where = f"{path.name}"
        _require(raw, MODEL_REQUIRED, where)
        if raw["status"] not in STATES:
            raise DefinitionError(f"{where}: invalid status {raw['status']!r}")
        if not NAME_RE.match(raw["name"]):
            raise DefinitionError(f"{where}: invalid model name {raw['name']!r}")

        base = raw["base"]
        if base["table"] not in table_names:
            raise DefinitionError(f"{where}: base table {base['table']} is not a declared source")
        joins: dict[str, dict] = {}
        aliases = {base["alias"]}
        for j in raw["joins"]:
            alias = j["alias"]
            if not ALIAS_RE.match(alias) or alias in aliases:
                raise DefinitionError(f"{where}: bad or duplicate join alias {alias!r}")
            if "derived" in j:
                if j["derived"] not in derived:
                    raise DefinitionError(f"{where}: join {alias} uses unknown derived source {j['derived']!r}")
            elif j.get("table") not in table_names:
                raise DefinitionError(f"{where}: join {alias} table {j.get('table')!r} is not a declared source")
            if j.get("relationship") not in relationships:
                raise DefinitionError(f"{where}: join {alias} has no declared relationship ({j.get('relationship')!r})")
            if j["parent"] not in aliases:
                raise DefinitionError(f"{where}: join {alias} parent {j['parent']!r} is not declared before it")
            joins[alias] = j
            aliases.add(alias)
        for a in raw.get("always_joined", []):
            if a not in joins:
                raise DefinitionError(f"{where}: always_joined alias {a!r} unknown")

        dims: dict[str, dict] = {}
        for d in raw["dimensions"]:
            _require(d, DIMENSION_REQUIRED, f"{where} dimension {d.get('name')}")
            if d["name"] in dims or not NAME_RE.match(d["name"]):
                raise DefinitionError(f"{where}: bad or duplicate dimension {d['name']!r}")
            if d["type"] not in ("time", "categorical"):
                raise DefinitionError(f"{where}: dimension {d['name']} has invalid type")
            for a in d["requires"]:
                if a not in joins:
                    raise DefinitionError(f"{where}: dimension {d['name']} requires unknown alias {a!r}")
            if "data_type" in d and d["data_type"] not in DATA_TYPES:
                raise DefinitionError(f"{where}: dimension {d['name']} has invalid data_type {d['data_type']!r}")
            d.setdefault("data_type", "timestamp" if d["type"] == "time" else "string")
            if not isinstance(d.setdefault("personal_data", False), bool):
                raise DefinitionError(f"{where}: dimension {d['name']} personal_data must be a boolean")
            dims[d["name"]] = d

        measures: dict[str, dict] = {}
        for m in raw["measures"]:
            _require(m, MEASURE_REQUIRED, f"{where} measure {m.get('name')}")
            if m["name"] in measures or m["name"] in dims or not NAME_RE.match(m["name"]):
                raise DefinitionError(f"{where}: bad or duplicate measure {m['name']!r}")
            if m["status"] not in STATES:
                raise DefinitionError(f"{where}: measure {m['name']} has invalid status")
            for a in m["requires"]:
                if a not in joins:
                    raise DefinitionError(f"{where}: measure {m['name']} requires unknown alias {a!r}")
            for cid in m["validation"]:
                if cid not in check_ids:
                    raise DefinitionError(f"{where}: measure {m['name']} references unknown validation check {cid!r}")
            m["_allowed"] = _parse_allowed(where, m, dims)
            measures[m["name"]] = m

        if raw["time_dimension"] not in dims:
            raise DefinitionError(f"{where}: time_dimension {raw['time_dimension']!r} is not a dimension")
        if raw["name"] in models:
            raise DefinitionError(f"duplicate model {raw['name']!r}")
        models[raw["name"]] = Model(raw=raw, name=raw["name"], joins=joins, dimensions=dims, measures=measures)

    return Definitions(sources=sources, derived=derived, reference=reference, relationships=relationships,
                       blocked_columns=blocked, models=models, root=root, allow_draft=allow_draft,
                       check_ids=check_ids, policy=policy)
