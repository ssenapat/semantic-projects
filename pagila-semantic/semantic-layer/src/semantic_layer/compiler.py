"""Compiles a structured semantic request into one parameterised, read-only SELECT (spec 10, 13)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .errors import DefinitionError, RequestError, UnsupportedRequest
from .loader import Definitions, Model

GRANULARITY_SQL = {"day": None, "week": "week", "month": "month", "quarter": "quarter", "year": "year"}
SCALAR_OPS = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
LIST_OPS = {"in": "IN", "not_in": "NOT IN"}
NULL_OPS = {"is_null": "IS NULL", "is_not_null": "IS NOT NULL"}
TZ_RE = re.compile(r"^[A-Za-z0-9_+\-/]{1,64}$")
ALLOWED_KEYS = {"model", "measures", "dimensions", "filters", "order_by", "limit", "timezone", "accept_fan_out"}
UNKNOWN_LABEL = "Unknown"


@dataclass
class Compiled:
    model: Model
    sql: str
    params: list[Any]
    columns: list[str]
    limit: int
    caveats: list[str] = field(default_factory=list)
    definitions_used: dict[str, str] = field(default_factory=dict)
    time_granularity: str | None = None
    measure_names: list[str] = field(default_factory=list)
    dimension_names: list[str] = field(default_factory=list)
    column_meta: list[dict] = field(default_factory=list)
    measure_versions: list[dict] = field(default_factory=list)
    caveat_codes: list[str] = field(default_factory=list)


def _scalar(value: Any, where: str) -> Any:
    if isinstance(value, (str, int, float, bool)) and value is not None:
        return value
    raise RequestError(f"{where}: filter values must be strings, numbers or booleans")


def _parse_date(value: Any, where: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise RequestError(f"{where}: filter value is not a YYYY-MM-DD date") from None


class Compiler:
    def __init__(self, defs: Definitions):
        self.defs = defs

    # ---------------------------------------------------------------- request checks
    def _model(self, request: dict) -> Model:
        extra = set(request) - ALLOWED_KEYS
        if extra:
            raise RequestError(f"unknown request fields: {sorted(extra)}")
        name = request.get("model")
        model = self.defs.models.get(name) if isinstance(name, str) else None
        if model is None or not self.defs.is_exposed(model.raw):
            raise RequestError(f"unknown model {name!r}; available: {sorted(m for m, v in self.defs.models.items() if self.defs.is_exposed(v.raw))}")
        return model

    def _measure(self, model: Model, name: Any) -> dict:
        m = model.measures.get(name) if isinstance(name, str) else None
        if m is None:
            raise RequestError(f"unknown measure {name!r} for model {model.name}; available: {self.exposed_measures(model)}")
        if not self.defs.is_exposed(m, model.raw):
            if m.get("expose") is False or m["status"] == "draft":
                raise RequestError(f"measure {name!r} is not available: it is {m['status']} and not approved for use "
                                   f"({m['description']})")
            raise RequestError(f"measure {name!r} is not available (status {m['status']})")
        return m

    def _dimension(self, model: Model, name: Any) -> dict:
        d = model.dimensions.get(name) if isinstance(name, str) else None
        if d is None:
            hint = ""
            if isinstance(name, str) and name.lower() in self.defs.blocked_columns | {"phone", "email", "address", "password"}:
                hint = " (personal data is never available)"
            raise RequestError(f"unknown dimension {name!r} for model {model.name}{hint}; available: {self.exposed_dimensions(model)}")
        if not self.defs.is_exposed(d, model.raw):
            raise RequestError(f"dimension {name!r} is not available (status {d.get('status', 'draft')})")
        return d

    def exposed_measures(self, model: Model) -> list[str]:
        return [n for n, m in model.measures.items() if self.defs.is_exposed(m, model.raw)]

    def exposed_dimensions(self, model: Model) -> list[str]:
        return [n for n, d in model.dimensions.items() if self.defs.is_exposed(d, model.raw)]

    # ---------------------------------------------------------------- joins
    def _needed_aliases(self, model: Model, requires: set[str]) -> list[str]:
        needed = set(model.raw.get("always_joined", [])) | requires
        for alias in list(needed):
            parent = model.joins[alias]["parent"]
            while parent in model.joins:
                needed.add(parent)
                parent = model.joins[parent]["parent"]
        return [a for a in model.joins if a in needed]

    def _join_sql(self, model: Model, alias: str) -> str:
        j = model.joins[alias]
        kind = "LEFT JOIN" if j.get("type") == "left" else "JOIN"
        if "derived" in j:
            target = f"({self.defs.derived[j['derived']]['sql']}) AS {alias}"
        else:
            target = f"{j['table']} {alias}"
        return f"{kind} {target} ON {j['condition']}"

    # ---------------------------------------------------------------- compile
    def _resolve_measures(self, model: Model, names: list[str]) -> list[tuple[Model, dict]]:
        """R1c / R1d: find every measure; return (owning model, measure) in request order."""
        owners: list[tuple[Model, dict]] = []
        for n in names:
            if n in model.measures:
                owners.append((model, model.measures[n]))
                continue
            others = [(mm, mm.measures[n]) for mm in self.defs.models.values()
                      if mm is not model and self.defs.is_exposed(mm.raw) and n in mm.measures
                      and self.defs.is_exposed(mm.measures[n], mm.raw)]
            if not others:
                available = [x for mm in self.defs.models.values() if self.defs.is_exposed(mm.raw)
                             for x in self.exposed_measures(mm)]
                raise RequestError(f"unknown measure {n!r}; available: {available}")
            owners.append(others[0])
        for (owner, _), n in zip(owners, names):
            if owner is model:
                self._measure(model, n)          # draft / unapproved -> "not approved for use"
        return owners

    def _check_measure_combination(self, model: Model, names: list[str], owners: list[tuple[Model, dict]]) -> list[dict]:
        """R2a, R2b, R2c."""
        owner_names = list(dict.fromkeys(o.name for o, _ in owners))
        if len(owner_names) >= 2:
            measure_models = {n: o.name for n, (o, _) in zip(names, owners)}
            listing = "; ".join(f"{n!r} is in model {m!r}" for n, m in measure_models.items())
            raise UnsupportedRequest(
                f"measures come from different models ({listing}); they cannot be combined, ask as separate questions",
                "mixed_models", {"measure_models": measure_models})
        if owner_names[0] != model.name:
            raise RequestError(f"measures {names} belong to model {owner_names[0]!r}, not {model.name!r}")
        measures = [m for _, m in owners]
        tags = {n: m["combinable_with"] for n, m in zip(names, measures)}
        if len(set(tags.values())) > 1:
            raise UnsupportedRequest(f"measures cannot be combined: their combinable_with tags differ {tags}",
                                     "not_combinable", {"combinable_with": tags})
        return measures

    def compile(self, request: dict) -> Compiled:
        if not isinstance(request, dict):
            raise RequestError("request must be a JSON object")
        policy = self.defs.policy
        if policy is None:
            raise DefinitionError("query policy is not loaded")
        model = self._model(request)                                           # R0

        measure_names = request.get("measures")
        if not isinstance(measure_names, list) or not measure_names or not all(isinstance(x, str) for x in measure_names):
            raise RequestError("'measures' must be a non-empty list of measure names")
        if len(measure_names) > policy.max_measures_per_query:                 # R1a
            raise RequestError(f"too many measures: {len(measure_names)} given, "
                               f"max_measures_per_query is {policy.max_measures_per_query}")
        if len(set(measure_names)) != len(measure_names):                      # R1b
            dupes = sorted({n for n in measure_names if measure_names.count(n) > 1})
            raise RequestError(f"duplicate measures: {dupes}")
        owners = self._resolve_measures(model, measure_names)                  # R1c, R1d
        measures = self._check_measure_combination(model, measure_names, owners)   # R2a-R2c

        dim_specs = request.get("dimensions", [])
        if not isinstance(dim_specs, list) or not all(isinstance(x, str) for x in dim_specs):
            raise RequestError("'dimensions' must be a list of strings")
        if len(dim_specs) > policy.max_dimensions_per_query:                   # R1e
            raise RequestError(f"too many dimensions: {len(dim_specs)} given, "
                               f"the maximum is {policy.max_dimensions_per_query}")
        base_names = [s.partition(":")[0] for s in dim_specs]
        for n in base_names:                                                   # R1f
            if base_names.count(n) > 1:
                raise RequestError(f"duplicate dimension {n!r}")
        dims: list[tuple[dict, str | None]] = []
        output_names: list[str] = []
        for spec in dim_specs:
            name, _, gran = spec.partition(":")
            d = self._dimension(model, name)
            if d["type"] == "time":
                gran = gran or "day"
                if gran not in d["granularities"]:
                    raise RequestError(f"dimension {name!r} supports granularities {d['granularities']}, not {gran!r}")
            elif gran:
                raise RequestError(f"dimension {name!r} is not a time dimension, so ':{gran}' is not allowed")
            else:
                gran = None
            dims.append((d, gran))
            output_names.append(name)
        overlap = set(output_names) & set(measure_names)
        if overlap:
            raise RequestError(f"names used as both dimension and measure: {sorted(overlap)}")

        filters = request.get("filters", [])
        if not isinstance(filters, list):
            raise RequestError("'filters' must be a list")
        filter_dims: list[dict] = []
        for idx, f in enumerate(filters):
            if not isinstance(f, dict) or set(f) - {"dimension", "op", "value"}:
                raise RequestError(f"filters[{idx}] must have only dimension, op, value")
            filter_dims.append(self._dimension(model, f.get("dimension")))

        tz = request.get("timezone", "UTC")
        if not isinstance(tz, str) or not TZ_RE.match(tz):
            raise RequestError("'timezone' must be a time zone name such as 'UTC' or 'America/Chicago'")

        limit = request.get("limit", policy.default_limit)
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
            raise RequestError("'limit' must be a positive integer")
        capped = limit > policy.max_rows
        limit = min(limit, policy.max_rows)

        # ----- R3: every output and filter dimension must be allowed for every measure
        for d in [d for d, _ in dims] + filter_dims:
            blocking = [n for n, m in zip(measure_names, measures) if d["name"] not in m["_allowed"]]
            if blocking:
                raise UnsupportedRequest(
                    f"dimension {d['name']!r} is not allowed for measure(s) {blocking}",
                    "dimension_not_allowed", {"dimension": d["name"], "blocking_measures": blocking})
        # ----- R4: the time grain must be allowed for every measure
        for d, gran in dims:
            if d["type"] != "time":
                continue
            blocking = [n for n, m in zip(measure_names, measures)
                        if m["_allowed"][d["name"]] is not None and gran not in m["_allowed"][d["name"]]]
            if blocking:
                raise UnsupportedRequest(
                    f"grain {gran!r} of dimension {d['name']!r} is not allowed for measure(s) {blocking}",
                    "grain_not_allowed", {"dimension": d["name"], "grain": gran, "blocking_measures": blocking})

        where_parts: list[str] = []
        where_params: list[Any] = []
        for idx, (f, d) in enumerate(zip(filters, filter_dims)):
            sql, params = self._filter_sql(d, f.get("op"), f.get("value"), tz, f"filters[{idx}]")
            where_parts.append(sql)
            where_params.extend(params)

        # ----- R5: fan-out and allocation rules
        used_dims = [d for d, _ in dims] + filter_dims
        fan_out = {d["name"] for d in used_dims if d.get("fan_out")}
        allocated_dims = [d for d in used_dims if d.get("allocated")]
        if allocated_dims and fan_out:
            raise RequestError("category (allocated) and actor (fan-out) cannot be combined in one request")
        if len(fan_out) > policy.max_fan_out_dimensions and not request.get("accept_fan_out"):
            raise RequestError(f"more than one fan-out dimension ({sorted(fan_out)}); set accept_fan_out to accept inflated totals")

        # ----- aliases
        requires: set[str] = set()
        for d in used_dims:
            requires |= set(d["requires"])
        for m in measures:
            requires |= set(m["requires"])
        aliases = self._needed_aliases(model, requires)
        alloc = model.raw.get("allocation")
        allocated = bool(alloc and alloc["trigger_alias"] in aliases)

        # ----- select list
        select_parts: list[str] = []
        select_params: list[Any] = []
        for (d, gran), name in zip(dims, output_names):
            expr = self._dimension_sql(d, gran)
            if d["type"] == "time":
                select_params.append(tz)
            elif d.get("null_policy") == "label_unknown":
                expr = f"COALESCE({expr}, '{UNKNOWN_LABEL}')"
            select_parts.append(f'{expr} AS "{name}"')
        unallocated_distinct: list[str] = []
        for m in measures:
            sql = m["sql"]
            if allocated:
                if "allocated_sql" in m:
                    sql = m["allocated_sql"]
                else:
                    unallocated_distinct.append(m["name"])
            select_parts.append(f'{sql} AS "{m["name"]}"')

        # ----- assemble: one SELECT, one GROUP BY (I1)
        base = model.raw["base"]
        from_sql = f"FROM {base['table']} {base['alias']}"
        join_sql = [self._join_sql(model, a) for a in aliases]
        group_sql = ""
        if dims:
            group_sql = " GROUP BY " + ", ".join(str(i + 1) for i in range(len(dims)))
        order_sql = self._order_sql(request.get("order_by"), output_names, measure_names)

        sql = "SELECT " + ", ".join(select_parts) + " " + from_sql
        if join_sql:
            sql += " " + " ".join(join_sql)
        if where_parts:
            sql += " WHERE " + " AND ".join(where_parts)
        sql += group_sql + order_sql + f" LIMIT {limit + 1}"
        params = select_params + where_params

        # ----- caveats (text keeps its wording; each carries a CONTRACT 3.5 code)
        caveats: list[str] = []
        codes: list[str] = []

        def add(code: str, text: str) -> None:
            caveats.append(text)
            if code not in codes:
                codes.append(code)

        if capped:
            add("limit_capped", f"limit reduced to the maximum of {policy.max_rows} rows")
        if allocated:
            add("category_allocation",
                "Category figures are allocated: a film in n categories contributes 1/n to each, so counts can be "
                "fractional and category rows add up to the total.")
            if unallocated_distinct:
                add("unallocated_distinct",
                    f"{', '.join(unallocated_distinct)} are distinct counts and are not allocated, so they do not add up across categories.")
        if fan_out:
            add("actor_fan_out", f"Fan-out: {', '.join(sorted(fan_out))} multiplies rows. Each film counts fully for every actor, so rows do not add up to the overall total.")
        if any(d.get("caveat_when_used") == "payment_date" for d in used_dims):
            add("payment_date", "payment_date is when the payment was recorded. It is often before the rental it pays for, so it is not the revenue date. "
                                "Revenue is normally dated by rental_date.")
        if model.name == "payment_activity" and any(d["name"] in ("payment_store", "payment_film", "payment_category") for d in used_dims):
            add("unpaid_rentals", "Revenue by store, film or category only includes rentals that have a payment; unpaid rentals contribute nothing.")
        for (d, _), n in zip(dims, output_names):
            if d.get("null_policy") == "label_unknown":
                add("null_labels", f"Missing values of {n} are shown as '{UNKNOWN_LABEL}'.")
        if "payment_staff" in output_names or "payment_store" in output_names or "store_id" in output_names:
            add("test_stores_hidden", "Only stores that hold inventory and staff who appear on payments exist in this data; the rest are test data and are never listed.")
        if any(m["name"] == "avg_rental_days" for m in measures):
            add("open_rentals_excluded", "avg_rental_days covers returned rentals only; open rentals are excluded.")
        personal = [n for (d, _), n in zip(dims, output_names) if d.get("personal_data")]
        if personal:
            add("personal_data", f"{', '.join(personal)} identifies people; contact details such as email, phone and address are never returned.")

        used = {f"model:{model.name}": model.version}
        for m in measures:
            used[f"measure:{m['name']}"] = m["version"]
        for d in used_dims:
            used[f"dimension:{d['name']}"] = model.version
        gran0 = next((g for (_, g) in dims if g in ("week", "month", "quarter", "year")), None)
        column_meta = [{"name": n, "role": "dimension", "label": d["label"], "unit": None, "type": d["data_type"]}
                       for (d, _), n in zip(dims, output_names)]
        column_meta += [{"name": m["name"], "role": "measure", "label": m["label"], "unit": m.get("unit"), "type": "number"}
                        for m in measures]
        return Compiled(model=model, sql=sql, params=params, columns=output_names + measure_names, limit=limit,
                        caveats=caveats, definitions_used=used, time_granularity=gran0,
                        measure_names=list(measure_names), dimension_names=output_names, column_meta=column_meta,
                        measure_versions=[{"name": m["name"], "version": m["version"]} for m in measures],
                        caveat_codes=codes)

    # ---------------------------------------------------------------- pieces
    def _dimension_sql(self, d: dict, gran: str | None) -> str:
        expr = d["expression"]
        if d["type"] != "time":
            return expr
        local = f"({expr} AT TIME ZONE %s)"
        unit = GRANULARITY_SQL[gran or "day"]
        if unit is None:
            return f"{local}::date"
        return f"date_trunc('{unit}', {local})::date"

    def _filter_sql(self, d: dict, op: Any, value: Any, tz: str, where: str) -> tuple[str, list[Any]]:
        expr = d["expression"]
        is_time = d["type"] == "time"
        if not isinstance(op, str):
            raise RequestError(f"{where}: 'op' is required")
        if op in NULL_OPS:
            return f"{expr} {NULL_OPS[op]}", []
        if op == "between":
            if not isinstance(value, list) or len(value) != 2:
                raise RequestError(f"{where}: 'between' needs a two-item list")
            lo, hi = value
            if is_time:
                lo, hi = _parse_date(lo, where), _parse_date(hi, where)
                if lo > hi:
                    raise RequestError(f"{where}: range start is after its end")
                return (f"({expr} >= (%s::date)::timestamp AT TIME ZONE %s AND {expr} < ((%s::date) + 1)::timestamp AT TIME ZONE %s)",
                        [lo, tz, hi, tz])
            return f"({expr} BETWEEN %s AND %s)", [_scalar(lo, where), _scalar(hi, where)]
        if op in LIST_OPS:
            if is_time or not isinstance(value, list) or not value or len(value) > 200:
                raise RequestError(f"{where}: '{op}' needs a non-empty list (max 200) on a non-time dimension")
            marks = ", ".join(["%s"] * len(value))
            return f"{expr} {LIST_OPS[op]} ({marks})", [_scalar(v, where) for v in value]
        if op in SCALAR_OPS:
            if is_time:
                day = _parse_date(value, where)
                local = f"(%s::date)::timestamp AT TIME ZONE %s"
                nxt = f"((%s::date) + 1)::timestamp AT TIME ZONE %s"
                if op == "eq":
                    return f"({expr} >= {local} AND {expr} < {nxt})", [day, tz, day, tz]
                if op == "ne":
                    return f"({expr} < {local} OR {expr} >= {nxt})", [day, tz, day, tz]
                if op == "gte":
                    return f"{expr} >= {local}", [day, tz]
                if op == "gt":
                    return f"{expr} >= {nxt}", [day, tz]
                if op == "lt":
                    return f"{expr} < {local}", [day, tz]
                return f"{expr} < {nxt}", [day, tz]     # lte
            return f"{expr} {SCALAR_OPS[op]} %s", [_scalar(value, where)]
        raise RequestError(f"{where}: unsupported op {op!r}; use one of "
                           f"{sorted(SCALAR_OPS) + sorted(LIST_OPS) + sorted(NULL_OPS) + ['between']}")

    def _order_sql(self, order_by: Any, dim_names: list[str], measure_names: list[str]) -> str:
        """I2: default = first measure DESC; explicit keys first; remaining dimensions ASC; NULLS LAST on every key."""
        if order_by is not None and not isinstance(order_by, list):
            raise RequestError("'order_by' must be a list")
        keys: list[tuple[str, str]] = []
        valid = set(dim_names) | set(measure_names)
        for item in order_by or []:
            if not isinstance(item, dict) or set(item) - {"field", "direction"}:
                raise RequestError("each order_by item needs 'field' and optional 'direction'")
            field_name = item.get("field")
            if field_name not in valid:
                raise RequestError(f"order_by field {field_name!r} must be one of the requested dimensions or measures")
            direction = str(item.get("direction", "asc")).lower()
            if direction not in ("asc", "desc"):
                raise RequestError("order_by direction must be 'asc' or 'desc'")
            if field_name not in [k for k, _ in keys]:
                keys.append((field_name, direction.upper()))
        if not keys:
            keys.append((measure_names[0], "DESC"))
        for n in dim_names:
            if n not in [k for k, _ in keys]:
                keys.append((n, "ASC"))
        return " ORDER BY " + ", ".join(f'"{k}" {d} NULLS LAST' for k, d in keys)
