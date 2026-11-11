#!/usr/bin/env python3
"""Contract conformance (CONTRACT §9). Exit 0 = conforms, 1 = findings, 2 = usage/environment.

--static  imports each service's shared models (§3), asserts model_json_schema() equality across copies,
          and that /openapi.json of each service contains every §4 path.
--responses  runs the stub-LLM stack in-process (no sockets, in-memory fake DB) and validates the responses of
          /api/v1/intent/parse (3 outcomes), /api/v1/query/execute (result, unsupported, clarification) and
          /api/v1/ask against the CONTRACT JSON shapes (C-2.1, C-2.2, C-2.4, C-2.5, C-2.7), all with
          additionalProperties false; also asserts the C-4.1 max_metrics_per_query agreement.
--live    with the services running on 127.0.0.1, calls each route and validates the response (§4 models and
          the error envelope). Needs no credentials; /api/v1/ask is only probed with a request that stops
          before any database access.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import tempfile
from pathlib import Path

TS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))   # fakes.py

SERVICES = {
    "retriever": ("semantic-retrieval-service", "semantic_retrieval_service", "http://127.0.0.1:8080"),
    "parser": ("llm-intent-parser-service", "llm_intent_parser_service", "http://127.0.0.1:8081"),
    "query": ("semantic-query-service", "semantic_query_service", "http://127.0.0.1:8082"),
}
COMMON = ["/health/live", "/health/ready", "/metrics"]
PATHS = {
    "retriever": ["/api/v1/ask", "/api/v1/retrieve", "/api/v1/catalog/status", "/api/v1/catalog/metrics"] + COMMON,
    "parser": ["/api/v1/intent/parse"] + COMMON,
    "query": ["/api/v1/query/execute"] + COMMON,
}
# Shared model name -> (service -> module under <package>.app.schemas).
SHARED = {
    "ErrorEnvelope": {"retriever": "errors", "parser": "response", "query": "errors"},
    "AuthorizationContext": {"retriever": "ask", "query": "semantic_request"},
    "SemanticGroupBy": {"retriever": "parser", "parser": "response", "query": "semantic_request"},
    "SemanticFilter": {"retriever": "parser", "parser": "response", "query": "semantic_request"},
    "SemanticDateRange": {"retriever": "parser", "parser": "response", "query": "semantic_request"},
    "ConstrainedSemanticRequest": {"retriever": "parser", "parser": "response", "query": "semantic_request"},
    "ClarificationDetails": {"retriever": "parser", "parser": "response", "query": "semantic_request"},
    "UnsupportedDetails": {"retriever": "parser", "parser": "response", "query": "semantic_request"},
    "ParserOptions": {"retriever": "parser", "parser": "request"},
    "IntentParserRequest": {"retriever": "parser", "parser": "request"},
    "LlmSafeMetric": {"retriever": "retrieval", "parser": "context"},
    "LlmSafeDimension": {"retriever": "retrieval", "parser": "context"},
    "LlmSafeGlossaryEntry": {"retriever": "retrieval", "parser": "context"},
    "LlmSafeAmbiguityRule": {"retriever": "retrieval", "parser": "context"},
    "LlmSemanticContext": {"retriever": "retrieval", "parser": "context"},
    "SemanticRequestOutcome": {"retriever": "parser", "parser": "response"},
    "ClarificationOutcome": {"retriever": "parser", "parser": "response"},
    "UnsupportedOutcome": {"retriever": "parser", "parser": "response"},
    "QueryExecuteRequest": {"retriever": "result", "query": "semantic_request"},
    "ResultColumn": {"retriever": "result", "query": "result"},
    "ChartHint": {"retriever": "result", "query": "result"},
    "QueryResultMetadata": {"retriever": "result", "query": "result"},
    "QueryResultResponse": {"retriever": "result", "query": "result"},
    "QueryClarificationResponse": {"retriever": "result", "query": "semantic_request"},
    "QueryUnsupportedResponse": {"retriever": "result", "query": "semantic_request"},
}


def _load_app(svc: str):
    folder, pkg, _ = SERVICES[svc]
    path = str(TS / folder)
    if path not in sys.path:
        sys.path.insert(0, path)
    os.environ.setdefault("SEMANTIC_LAYER_SRC", str(TS / "semantic-layer" / "src"))
    os.environ.setdefault("SEMANTIC_LAYER_DEFINITIONS_DIR", str(TS / "semantic-layer" / "definitions"))
    os.environ.setdefault("PARSER_LLM_CLIENT", "stub")
    return importlib.import_module(f"{pkg}.app.main").app


def _add_path(svc: str) -> None:
    path = str(TS / SERVICES[svc][0])
    if path not in sys.path:
        sys.path.insert(0, path)


def _strip_defaults(node):
    """Recursively drops `default` and `required`: the producer (query service) always serialises every field, so a
    consumer copy (retriever) that requires a field the producer defaults accepts exactly the same payloads."""
    if isinstance(node, dict):
        return {k: _strip_defaults(v) for k, v in node.items() if k not in ("default", "required")}
    if isinstance(node, list):
        return [_strip_defaults(v) for v in node]
    return node


def _normalized(schema: dict) -> dict:
    """Drops differences that do not change what a consumer accepts from a producer: defaults, required lists
    (see _strip_defaults) and a top-level `additionalProperties`."""
    out = json.loads(json.dumps(schema))
    out.pop("additionalProperties", None)
    return _strip_defaults(out)


def _schema_diff(a: dict, b: dict) -> str:
    keys = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
    return ", ".join(keys) or "(unknown)"


WARNINGS: list[str] = []


def check_static() -> list[str]:
    findings: list[str] = []
    for model, where in SHARED.items():
        schemas: dict[str, dict] = {}
        for svc, mod in where.items():
            pkg = SERVICES[svc][1]
            _add_path(svc)
            try:
                m = importlib.import_module(f"{pkg}.app.schemas.{mod}")
                schemas[svc] = getattr(m, model).model_json_schema()
            except Exception as exc:  # noqa: BLE001
                findings.append(f"{model}@{svc}: cannot import ({type(exc).__name__})")
        names = list(schemas)
        for other in names[1:]:
            if schemas[other] == schemas[names[0]]:
                continue
            if _normalized(schemas[other]) == _normalized(schemas[names[0]]):
                WARNINGS.append(f"{model}: tolerated non-identical copy {names[0]} vs {other} "
                                f"({_schema_diff(schemas[names[0]], schemas[other])})")
            else:
                findings.append(f"{model}: schema differs between {names[0]} and {other} "
                                f"({_schema_diff(schemas[names[0]], schemas[other])})")
    for svc, expected in PATHS.items():
        try:
            have = set(_load_app(svc).openapi()["paths"])
        except Exception as exc:  # noqa: BLE001
            findings.append(f"{svc}: cannot build app/openapi ({type(exc).__name__}: {exc})")
            continue
        for p in expected:
            if p not in have:
                findings.append(f"{svc}: openapi is missing path {p}")
    return findings


# ---------------------------------------------------------------------------------------------------------
# CONTRACT JSON shapes (draft 2020-12), every object closed with additionalProperties false.
def _obj(properties: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "additionalProperties": False, "properties": properties,
            "required": list(properties) if required is None else required}


_STR = {"type": "string"}
_STR_ARRAY = {"type": "array", "items": _STR}
_SEMANTIC_REQUEST = _obj({
    "metrics": {"type": "array", "items": _STR, "minItems": 1, "maxItems": 5},                 # C-2.1 (MAX_METRICS = 5)
    "group_by": {"type": "array", "maxItems": 8, "items": _obj({
        "dimension": _STR, "time_grain": {"enum": ["day", "week", "month", "quarter", "year", None]}},
        ["dimension"])},
    "filters": {"type": "array", "maxItems": 20, "items": _obj({
        "dimension": _STR, "operator": {"enum": ["equals", "not_equals", "in", "not_in", "between", "contains"]},
        "values": {"type": "array", "items": _STR, "minItems": 1, "maxItems": 50}})},
    "date_range": {"oneOf": [_obj({"kind": {"enum": ["absolute", "relative"]},
                                   "start_date": {"type": ["string", "null"]}, "end_date": {"type": ["string", "null"]},
                                   "relative_period": {"type": ["string", "null"]}}, ["kind"]), {"type": "null"}]},
    "requested_limit": {"type": "integer", "minimum": 1, "maximum": 5000},
}, ["metrics"])
_CLARIFICATION = _obj({"question": {"type": "string", "minLength": 1, "maxLength": 500},
                       "ambiguity_type": {"enum": ["metric", "dimension", "entity", "airport_or_location",
                                                   "carrier_role", "time_interpretation", "scope"]},
                       "candidate_options": {"type": "array", "items": _STR, "maxItems": 10}})
_UNSUPPORTED = _obj({                                                                          # C-2.2
    "message": {"type": "string", "minLength": 1, "maxLength": 500},
    "reason": {"enum": ["no_approved_metric", "no_supported_dimension", "outside_retrieved_domains",
                        "insufficient_context", "unsupported_operation", "metrics_not_combinable"]},
    "suggested_supported_topics": {"type": "array", "items": _STR, "maxItems": 10},
    "conflicting_metrics": {"type": "array", "items": _STR, "maxItems": 5}})                   # always serialised
_NULL = {"type": "null"}
_CONF = {"type": "number", "minimum": 0, "maximum": 1}
PARSE_SHAPES = {                                                                               # C-2.4
    "semantic_request": _obj({"outcome": {"const": "semantic_request"}, "catalog_version": _STR, "confidence": _CONF,
                              "semantic_request": _SEMANTIC_REQUEST, "clarification": _NULL, "unsupported": _NULL}),
    "clarification_required": _obj({"outcome": {"const": "clarification_required"}, "catalog_version": _STR,
                                    "confidence": _CONF, "semantic_request": _NULL, "clarification": _CLARIFICATION,
                                    "unsupported": _NULL}),
    "unsupported": _obj({"outcome": {"const": "unsupported"}, "catalog_version": _STR, "confidence": _CONF,
                         "semantic_request": _NULL, "clarification": _NULL, "unsupported": _UNSUPPORTED}),
}
_COLUMN = _obj({"name": _STR, "role": {"enum": ["dimension", "metric"]}, "label": _STR,
                "unit": {"enum": ["count", "ratio", "percent", None]},
                "semantic_type": {"enum": ["dimension", "time_dimension", "metric"]}, "data_type": _STR})
_CHART_HINT = _obj({"chart_type": {"enum": ["line", "bar", "stacked_bar", "area", "scatter", "table", "kpi"]},
                    "x_field": {"type": ["string", "null"]}, "y_fields": _STR_ARRAY,
                    "color_field": {"type": ["string", "null"]}, "title": {"type": ["string", "null"]},
                    "confidence": _CONF}, ["chart_type", "confidence"])
RESULT_SHAPE = _obj({                                                                          # C-2.5
    "outcome": {"const": "result"}, "request_id": _STR,
    "columns": {"type": "array", "items": _COLUMN}, "rows": {"type": "array", "items": {"type": "array"}},
    "truncated": {"type": "boolean"}, "row_count": {"type": "integer", "minimum": 0},
    "metric_versions": {"type": "object", "additionalProperties": {"type": "integer"}},
    "metadata": _obj({"metrics": _STR_ARRAY, "catalog_version": _STR, "time_basis": {"type": ["string", "null"]},
                      "applied_filters": {"type": "array", "items": {"type": "object"}}, "generated_at_utc": _STR}),
    "chart_hints": {"type": "array", "items": _CHART_HINT}, "query_execution": {"type": "object"}})
QUERY_SHAPES = {
    "result": RESULT_SHAPE,
    "clarification_required": _obj({"outcome": {"const": "clarification_required"}, "request_id": _STR,
                                    "clarification": _CLARIFICATION}),
    "unsupported": _obj({"outcome": {"const": "unsupported"}, "request_id": _STR, "unsupported": _UNSUPPORTED}),
}
ASK_SHAPE = _obj({                                                                             # C-2.7
    "request_id": _STR, "catalog_version": _STR, "authorization_mode": _STR,
    "outcome": {"enum": ["result", "clarification_required", "unsupported", "error"]},
    "clarification": {"oneOf": [_CLARIFICATION, _NULL]}, "unsupported": {"oneOf": [_UNSUPPORTED, _NULL]},
    "result": {"oneOf": [RESULT_SHAPE, _NULL]}, "retrieval_summary": {"type": "object"}})


def _validate(name: str, body, schema: dict) -> list[str]:
    from jsonschema import Draft202012Validator

    errors = sorted(Draft202012Validator(schema).iter_errors(body), key=lambda e: list(e.absolute_path))
    return [f"{name}: {'/'.join(map(str, e.absolute_path)) or '<root>'}: {e.message[:160]}" for e in errors]


def check_max_metrics() -> list[str]:
    """C-4.1: the four max_metrics_per_query values are equal, and the compiler has no MAX_METRICS attribute."""
    findings: list[str] = []
    os.environ.setdefault("SEMANTIC_LAYER_SRC", str(TS / "semantic-layer" / "src"))
    os.environ.setdefault("SEMANTIC_LAYER_DEFINITIONS_DIR", str(TS / "semantic-layer" / "definitions"))
    for svc in SERVICES:
        _add_path(svc)
    src = os.environ["SEMANTIC_LAYER_SRC"]
    if src not in sys.path:
        sys.path.insert(0, src)
    values: dict[str, object] = {}
    try:
        for svc in SERVICES:
            mod = importlib.import_module(f"{SERVICES[svc][1]}.app.policy_limits")
            values[f"{svc} policy_limits.MAX_METRICS"] = mod.MAX_METRICS
        from semantic_layer import compiler
        from semantic_layer.loader import load_catalog

        catalog = load_catalog(Path(os.environ["SEMANTIC_LAYER_DEFINITIONS_DIR"]))
        values["catalog query_policy.max_metrics_per_query"] = catalog.query_policy.max_metrics_per_query
        if hasattr(compiler, "MAX_METRICS"):
            findings.append("C-4.1: semantic_layer.compiler still defines MAX_METRICS")
    except Exception as exc:  # noqa: BLE001
        return [f"C-4.1: cannot read the max_metrics_per_query values ({type(exc).__name__}: {exc})"]
    if len(set(values.values())) != 1:
        findings.append(f"C-4.1: max_metrics_per_query values differ: {values}")
    return findings


def check_responses() -> list[str]:
    """Drives the stub-LLM stack in-process and validates each response against its CONTRACT shape."""
    from fakes import open_fake_stack

    findings: list[str] = []
    with tempfile.TemporaryDirectory() as tmp, open_fake_stack(Path(tmp) / "audit.jsonl") as (retriever, parser, query, _db):
        # /api/v1/ask: result, clarification, unsupported.
        for question, outcome in (("Cancelled flights last month by carrier and origin airport", "result"),
                                  ("Show flight delays", "clarification_required"),
                                  ("Show passenger email addresses", "unsupported")):
            r = retriever.post("/api/v1/ask", json={"question": question})
            if r.status_code != 200:
                findings.append(f"/api/v1/ask {outcome}: HTTP {r.status_code}")
                continue
            findings += _validate(f"/api/v1/ask {outcome}", r.json(), ASK_SHAPE)
            if r.json().get("outcome") != outcome:
                findings.append(f"/api/v1/ask: expected outcome {outcome}, got {r.json().get('outcome')}")
        # /api/v1/intent/parse: the 3 outcomes, with the context the Retriever builds.
        for question, outcome in (("Flights and cancelled flights last month by carrier and origin airport, only for Chicago",
                                   "semantic_request"),
                                  ("Cancelled flights by airport", "clarification_required"),
                                  ("Show passenger email addresses", "unsupported")):
            ctx = retriever.post("/api/v1/retrieve", json={"question": question}).json()["semantic_context"]
            r = parser.post("/api/v1/intent/parse", json={"request_id": "cc-1", "question": question,
                                                          "semantic_context": ctx})
            if r.status_code != 200:
                findings.append(f"/api/v1/intent/parse {outcome}: HTTP {r.status_code}")
                continue
            findings += _validate(f"/api/v1/intent/parse {outcome}", r.json(), PARSE_SHAPES[outcome])
            if r.json().get("outcome") != outcome:
                findings.append(f"/api/v1/intent/parse: expected outcome {outcome}, got {r.json().get('outcome')}")
        # /api/v1/query/execute: result, unsupported, clarification.
        version = query.get("/health/ready").json()["catalog_version"]
        date_range = {"kind": "relative", "relative_period": "last_complete_calendar_month"}
        for label, sr, outcome in (
                ("result", {"metrics": ["flight_count", "cancelled_flight_count"],
                            "group_by": [{"dimension": "carrier_code"}], "date_range": date_range}, "result"),
                ("unsupported", {"metrics": ["cancellation_rate"], "group_by": [{"dimension": "cancellation_reason"}],
                                 "date_range": date_range}, "unsupported"),
                ("clarification", {"metrics": ["flight_count"], "group_by": [{"dimension": "airport"}],
                                   "date_range": date_range}, "clarification_required")):
            r = query.post("/api/v1/query/execute", json={"request_id": "cc-2", "catalog_version": version,
                                                          "semantic_request": sr})
            if r.status_code != 200:
                findings.append(f"/api/v1/query/execute {label}: HTTP {r.status_code}")
                continue
            findings += _validate(f"/api/v1/query/execute {label}", r.json(), QUERY_SHAPES[outcome])
            if r.json().get("outcome") != outcome:
                findings.append(f"/api/v1/query/execute: expected outcome {outcome}, got {r.json().get('outcome')}")
    return findings


def check_live() -> list[str]:
    import httpx

    findings: list[str] = []
    for svc in SERVICES:
        _load_app(svc)
    from semantic_retrieval_service.app.schemas.ask import AskResponse
    from semantic_retrieval_service.app.schemas.errors import ErrorEnvelope
    from semantic_retrieval_service.app.schemas.retrieval import MetricCatalogResponse

    def get(c, base, path):
        try:
            return c.get(base + path, timeout=10)
        except httpx.HTTPError as exc:
            findings.append(f"{base}{path}: unreachable ({type(exc).__name__})")
            return None

    with httpx.Client() as c:
        for svc, (_, _, base) in SERVICES.items():
            r = get(c, base, "/openapi.json")
            if r is not None and r.status_code == 200:
                for p in PATHS[svc]:
                    if p not in r.json().get("paths", {}):
                        findings.append(f"{svc}: live openapi is missing {p}")
            r = get(c, base, "/health/live")
            if r is not None and (r.status_code != 200 or r.json() != {"status": "ok"}):
                findings.append(f"{svc}: /health/live must be 200 {{'status':'ok'}}")
            r = get(c, base, "/health/ready")
            if r is not None and r.status_code not in (200, 503):
                findings.append(f"{svc}: /health/ready returned {r.status_code}")
            r = get(c, base, "/metrics")
            if r is not None and r.status_code != 200:
                findings.append(f"{svc}: /metrics returned {r.status_code}")
        base = SERVICES["retriever"][2]
        r = get(c, base, "/api/v1/catalog/metrics")
        if r is not None and r.status_code == 200:
            try:
                MetricCatalogResponse.model_validate(r.json())
            except Exception as exc:  # noqa: BLE001
                findings.append(f"retriever /catalog/metrics does not match MetricCatalogResponse ({type(exc).__name__})")
        # Error envelope: an invalid body must give 400/422 with the REQ-009 envelope on every service.
        for svc, path in (("retriever", "/api/v1/ask"), ("parser", "/api/v1/intent/parse"),
                          ("query", "/api/v1/query/execute")):
            try:
                r = c.post(SERVICES[svc][2] + path, json={}, timeout=10)
            except httpx.HTTPError as exc:
                findings.append(f"{svc}{path}: unreachable ({type(exc).__name__})")
                continue
            if r.status_code not in (400, 422):
                findings.append(f"{svc}{path}: empty body returned {r.status_code}, expected 400/422")
                continue
            try:
                ErrorEnvelope.model_validate(r.json())
            except Exception:  # noqa: BLE001
                findings.append(f"{svc}{path}: error body is not an ErrorEnvelope")
        # Question that the stub parser answers without touching the database.
        try:
            r = c.post(base + "/api/v1/ask", json={"question": "Show flight delays"}, timeout=60)
            if r.status_code == 200:
                body = AskResponse.model_validate(r.json())
                if body.outcome not in ("clarification_required", "unsupported", "result"):
                    findings.append("retriever /ask: unexpected outcome")
            else:
                ErrorEnvelope.model_validate(r.json())
        except Exception as exc:  # noqa: BLE001
            findings.append(f"retriever /ask: response does not match contract ({type(exc).__name__})")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--static", action="store_true")
    g.add_argument("--responses", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--strict", action="store_true", help="treat tolerated (compatible) differences as findings")
    args = ap.parse_args()
    if args.static:
        findings, mode = check_static() + check_max_metrics(), "static"
    elif args.responses:
        findings, mode = check_responses() + check_max_metrics(), "responses"
    else:
        findings, mode = check_live(), "live"
    for w in WARNINGS:
        print(f"  warning: {w}")
    if args.strict:
        findings += WARNINGS
    if findings:
        print(f"contract_check --{mode}: {len(findings)} finding(s)")
        for f in findings:
            print(f"  - {f}")
        return 1
    print(f"contract_check --{mode}: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
