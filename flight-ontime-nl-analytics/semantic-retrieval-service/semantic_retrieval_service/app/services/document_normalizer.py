"""Turns the SP-1 catalog into SemanticDocuments (REQ-013/014/015).

Only eligible entries (``catalog.is_exposable(status, audience)``) become documents. The
``llm_safe_payload`` is built from an allow-list of business fields; physical names, SQL and
join text only ever appear in ``internal_payload``.
"""
from __future__ import annotations

import re

from ..schemas.catalog import SemanticDocument

DOMAIN_RULES = [
    "Answer only with a metric, dimensions and filters listed in this context.",
    "If the question is ambiguous, ask a clarifying question and never guess.",
    "If the question needs data that is not available, say it is unsupported.",
    "Never produce SQL, table names, column names or joins.",
]

OPERATORS = {
    "time": ["equals", "between"],
    "categorical": ["equals", "not_equals", "in", "not_in"],
    "numeric": ["equals", "not_equals", "in", "not_in", "between"],
    "boolean": ["equals"],
}

# Business paraphrases (SP-1 definitions carry no synonyms), keyed by semantic name.
METRIC_SYNONYMS = {
    "flight_count": ["number of flights", "total flights", "flights", "flight volume"],
    "operated_flight_count": ["operated flights", "flights operated", "completed flights"],
    "cancelled_flight_count": ["cancellations", "cancelled flights", "canceled flights", "number of cancellations"],
    "cancellation_rate": ["cancel rate", "percent cancelled", "share of flights cancelled", "cancellation percentage"],
    "diverted_flight_count": ["diversions", "diverted flights", "number of diversions"],
    "diversion_rate": ["diversion percentage", "share of flights diverted"],
    "departure_delay_rate": ["late departure rate", "departure delays rate", "share of late departures"],
    "arrival_delay_rate": ["late arrival rate", "arrival delays rate", "share of late arrivals"],
    "on_time_arrival_rate": ["on time performance", "punctuality", "on time rate", "on time arrivals"],
    "departure_delayed_flight_count": ["late departures", "delayed departures", "number of delayed departures"],
    "arrival_delayed_flight_count": ["late arrivals", "delayed arrivals", "number of delayed arrivals"],
}
DIMENSION_SYNONYMS = {
    "flight_date": ["date", "day", "daily"],
    "day_of_week": ["weekday", "day of the week"],
    "month": ["monthly", "by month"],
    "quarter": ["quarterly"],
    "departure_time_block": ["departure time", "time of departure", "departure hour"],
    "arrival_time_block": ["arrival time", "time of arrival", "arrival hour"],
    "carrier_code": ["airline", "carrier", "airline code"],
    "origin_airport_code": ["departure airport", "origin airport", "from airport", "departing airport"],
    "origin_city": ["departure city", "origin city", "departing city"],
    "origin_state": ["departure state", "origin state", "departing state"],
    "destination_airport_code": ["arrival airport", "destination airport", "to airport"],
    "destination_city": ["arrival city", "destination city"],
    "destination_state": ["arrival state", "destination state"],
    "cancellation_reason": ["reason for cancellation", "why cancelled", "cancel reason"],
    "distance_group": ["distance band", "flight distance group"],
    "departure_delay_group": ["departure delay band"],
    "arrival_delay_group": ["arrival delay band"],
}

_UNSAFE = re.compile(r"(?i)\b(ontime|select|insert|update|delete|join|from\s+\w+\.\w+|tran-stats|fl_date)\b|\w+\.\w+\s*\(|[;`]")


def normalize_text(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", (text or "").lower())).strip()


def humanize(name: str) -> str:
    return name.replace("_", " ")


def safe_text(text: str | None, fallback: str = "") -> str:
    """Business text only: anything that looks like SQL / physical naming is replaced."""
    if not text or _UNSAFE.search(text):
        return fallback
    return text.strip()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i in items:
        k = normalize_text(i)
        if k and k not in seen:
            seen.add(k)
            out.append(i)
    return out


def _dtype(raw: str) -> str:
    if raw in ("time", "categorical", "boolean"):
        return raw
    return "numeric"  # ordinal, numeric


def _doc(doc_type, name, label, description, domain, model, status, versions, synonyms, safe_payload,
         internal, source_file, extra_text=()) -> SemanticDocument:
    synonyms = _dedupe(synonyms)
    searchable = " ".join(
        normalize_text(x) for x in [name, humanize(name), label, description, *synonyms, *extra_text] if x
    )
    return SemanticDocument(
        document_id=f"{doc_type}:{name}", document_type=doc_type, canonical_name=name, label=label,
        description=description, domain=domain, semantic_model=model, status=status,
        semantic_version=versions.semantic_version, catalog_version=versions.catalog_version,
        classification="internal", synonyms=synonyms, tags=[doc_type, domain, status],
        searchable_text=searchable, llm_safe_payload=safe_payload, internal_payload=internal,
        source_file=source_file, source_checksum=versions.source_checksum,
    )


def eligible_dimensions(catalog, audience: str) -> dict[str, tuple[str, object]]:
    """dimension name -> (model name, Dimension) for exposable dimensions of exposable models."""
    out = {}
    for model in catalog.models.values():
        if not catalog.is_exposable(model.status, audience):
            continue
        for d in model.dimensions:
            if catalog.is_exposable(d.status, audience):
                out.setdefault(d.name, (model.name, d))
    return out


def time_basis_label(catalog, metric) -> str | None:
    model = catalog.models.get(metric.model or "")
    if model is None or not metric.time_basis:
        return None
    for d in model.dimensions:
        if d.column == metric.time_basis or d.name == metric.time_basis:
            return d.label.lower()
    return None


def normalize(catalog, versions, audience: str) -> list[SemanticDocument]:
    docs: list[SemanticDocument] = []
    dims = eligible_dimensions(catalog, audience)
    metric_names: set[str] = set()

    # ---- metrics
    for metric in sorted(catalog.metrics.values(), key=lambda m: m.name):
        if not catalog.is_exposable(metric.status or "", audience):
            continue
        model = catalog.models.get(metric.model or "")
        if model is None or not catalog.is_exposable(model.status, audience):
            continue
        metric_names.add(metric.name)
        allowed = [d for d in (metric.allowed_dimensions or ()) if d in dims]
        td = model.time_dimension if model.time_dimension in allowed else None
        grains = list(model.dimension(td).granularities) if td else []
        synonyms = [metric.label or humanize(metric.name), humanize(metric.name), *METRIC_SYNONYMS.get(metric.name, [])]
        description = safe_text(metric.description, metric.label or humanize(metric.name))
        payload = {
            "name": metric.name, "label": metric.label, "description": description, "domain": model.name,
            "allowed_dimensions": allowed, "time_dimension": td, "allowed_time_grains": grains,
            "synonyms": _dedupe(synonyms), "clarification_notes": [],
            "combinable_with": metric.combinable_with,
        }
        internal = {"unit": metric.unit, "time_basis": time_basis_label(catalog, metric),
                    "caveats": [c for c in (safe_text(c) for c in metric.caveats) if c],
                    "metric_type": metric.type, "grain": metric.grain}
        docs.append(_doc("metric", metric.name, metric.label, description, model.name, model.name, metric.status,
                         versions, synonyms, payload, internal, "metrics/flight_operations_metrics.yml"))

    # ---- dimensions
    for name in sorted(dims):
        model_name, d = dims[name]
        synonyms = [d.label, humanize(d.name), *DIMENSION_SYNONYMS.get(d.name, [])]
        description = d.label if not safe_text(d.note) else safe_text(d.note)
        payload = {
            "name": d.name, "label": d.label, "description": description, "type": _dtype(d.type),
            "synonyms": _dedupe(synonyms), "allowed_operators": OPERATORS[_dtype(d.type)],
            "allowed_time_grains": list(d.granularities), "clarification_notes": [],
        }
        internal = {"column": d.column, "source": d.source, "mapping": d.mapping, "role": d.role}
        docs.append(_doc("dimension", d.name, d.label, description, model_name, model_name, d.status, versions,
                         synonyms, payload, internal, "semantic_models/flight_operations.yml"))

    domain = next(iter(sorted(m.name for m in catalog.models.values() if catalog.is_exposable(m.status, audience))), "")
    known = metric_names | set(dims)
    policy = catalog.query_policy
    glossary_terms: set[str] = set()

    # ---- policy glossary terms (C-3.1): plain-language terms mapped to catalog names
    for gt in getattr(policy, "glossary_terms", ()):
        term = gt.term
        if term in glossary_terms or term in known:
            continue
        glossary_terms.add(term)
        meaning = safe_text(gt.meaning, "Catalog term.")
        entry = {"term": term, "meaning": meaning, "maps_to": list(gt.maps_to), "ambiguity_action": "none",
                 "clarification_question_template": None}
        docs.append(_doc("glossary", term, term, meaning, domain, domain, "validated", versions,
                         [term], entry, {"maps_to": list(gt.maps_to)}, "policies/query_policy.yml"))

    # ---- ambiguity rules + ambiguous-term glossary entries
    for amb in policy.ambiguous_terms:
        alts = [o for o in amb.options if o in known]
        question = "Which did you mean: " + ", ".join(humanize(a) for a in alts) + "?" if alts else "Please clarify."
        rule = {"trigger": amb.reason_code, "alternatives": alts, "required_action": "clarify",
                "question_template": question}
        docs.append(_doc("ambiguity_rule", amb.reason_code, humanize(amb.reason_code), question, domain, domain,
                         "validated", versions, [], rule, {"terms": list(amb.terms)}, "policies/query_policy.yml",
                         extra_text=[humanize(t) for t in amb.terms]))
        for term in amb.terms:
            if term in glossary_terms or term in known:
                continue
            glossary_terms.add(term)
            entry = {"term": humanize(term), "meaning": f"Ambiguous term: {humanize(amb.reason_code)}",
                     "maps_to": alts, "ambiguity_action": "clarify", "clarification_question_template": question}
            docs.append(_doc("glossary", term, humanize(term), entry["meaning"], domain, domain, "validated",
                             versions, [humanize(term)], entry, {"rule": amb.reason_code}, "policies/query_policy.yml"))

    # ---- unavailable concepts
    for con in policy.unavailable_concepts:
        reason = safe_text(con.reason.replace("tran-stats", "the data"), "This information is not available.")
        reason = reason if not _UNSAFE.search(reason) else "This information is not available."
        terms = [humanize(t) for t in con.terms]
        question = f"{reason} (terms: {', '.join(terms[:40])})"
        rule = {"trigger": con.reason_code, "alternatives": [], "required_action": "reject",
                "question_template": question}
        docs.append(_doc("unavailable_concept", con.reason_code, humanize(con.reason_code), reason, domain, domain,
                         "validated", versions, terms, rule, {"terms": list(con.terms)},
                         "policies/query_policy.yml", extra_text=terms))
        for term in con.terms:
            if term in glossary_terms or term in known:
                continue
            glossary_terms.add(term)
            entry = {"term": humanize(term), "meaning": reason, "maps_to": [], "ambiguity_action": "reject",
                     "clarification_question_template": None}
            docs.append(_doc("glossary", term, humanize(term), reason, domain, domain, "validated", versions,
                             [humanize(term)], entry, {"rule": con.reason_code}, "policies/query_policy.yml"))

    # ---- metric synonyms as glossary entries
    for d in [x for x in docs if x.document_type == "metric"]:
        for syn in METRIC_SYNONYMS.get(d.canonical_name, []):
            key = normalize_text(syn).replace(" ", "_")
            if key in glossary_terms:
                continue
            glossary_terms.add(key)
            entry = {"term": syn, "meaning": f"Means the metric {d.label}", "maps_to": [d.canonical_name],
                     "ambiguity_action": "none", "clarification_question_template": None}
            docs.append(_doc("glossary", key, syn, entry["meaning"], d.domain, d.semantic_model, d.status, versions,
                             [syn], entry, {"metric": d.canonical_name}, "metrics/flight_operations_metrics.yml"))

    # ---- code-mapping labels (eligible mappings only)
    for mp in sorted(catalog.mappings.values(), key=lambda m: m.name):
        if not catalog.is_exposable(mp.status, audience):
            continue
        for key, label in sorted(mp.values.items(), key=lambda kv: str(kv[0])):
            term = normalize_text(str(label)).replace(" ", "_")
            if not term or term in glossary_terms:
                continue
            glossary_terms.add(term)
            entry = {"term": str(label), "meaning": f"Display name for code {key}", "maps_to": [],
                     "ambiguity_action": "none", "clarification_question_template": None}
            docs.append(_doc("glossary", term, str(label), entry["meaning"], domain, domain, mp.status, versions,
                             [str(label)], entry, {"mapping": mp.name}, "semantic_models/code_mappings.yml"))
    return docs
