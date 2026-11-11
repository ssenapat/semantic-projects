"""Grounding and safety validation of a parsed outcome against the supplied context (LLD C2, CONTRACT 6.2)."""
import re
from dataclasses import dataclass, field
from datetime import date

from ..schemas.context import LlmSemanticContext
from ..schemas.request import ParserOptions
from ..schemas.response import (ClarificationDetails, ClarificationOutcome, SemanticRequestOutcome,
                               UnsupportedDetails, UnsupportedOutcome)
from .text import normalize

LEAKAGE_RE = re.compile(r"(?i)\b(SELECT|FROM|JOIN|WHERE|INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|WITH)\b\s+\S")
LEAKAGE_TOKENS = (";", "--", "```", "/*")
# Context text (descriptions) legitimately uses words like "from"; only flag statement-shaped SQL and code markers.
_CONTEXT_CODE_RE = re.compile(
    r"(?i)\bSELECT\b[^.]*\bFROM\b|\b(DROP|ALTER|TRUNCATE|CREATE)\s+(TABLE|DATABASE|SCHEMA|VIEW|INDEX)\b"
    r"|\bINSERT\s+INTO\b|\bDELETE\s+FROM\b|\bUPDATE\s+\w+\s+SET\b|```|/\*")


def has_sql_leakage(text: str) -> bool:
    return bool(LEAKAGE_RE.search(text)) or any(t in text for t in LEAKAGE_TOKENS)


def has_context_code(text: str) -> bool:
    return bool(_CONTEXT_CODE_RE.search(text))


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    leakage: bool = False
    # Kind B (C-2.4.1): a deterministic UnsupportedOutcome built by the parser; wins over Kind A errors.
    deterministic: UnsupportedOutcome | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


ROLE_QUALIFIERS = frozenset({"origin", "departure", "departing", "outbound",
                             "destination", "arrival", "arriving", "inbound"})


def _clarify_triggered(question: str, ctx: LlmSemanticContext) -> str | None:
    """First clarify trigger found in the question that is not role-qualified (A1)."""
    tokens = normalize(question).split()
    triggers = [r.trigger for r in ctx.ambiguity_rules if r.required_action == "clarify"]
    triggers += [g.term for g in ctx.glossary if g.ambiguity_action == "clarify"]
    for t in triggers:
        t_tokens = normalize(t).split()
        n = len(t_tokens)
        if not n:
            continue
        for i in range(len(tokens) - n + 1):
            if tokens[i:i + n] == t_tokens and not (i > 0 and tokens[i - 1] in ROLE_QUALIFIERS):
                return t
    return None


def clarification_from_context(question: str, ctx: LlmSemanticContext) -> ClarificationOutcome | None:
    """Deterministic clarification for a question that matches a clarify trigger (used when the model failed to
    return one itself, e.g. a typo such as 'depearture' defeated the role-qualifier check)."""
    trigger = _clarify_triggered(question, ctx)
    if trigger is None:
        return None
    t_norm = normalize(trigger)
    text, options = None, []
    for r in ctx.ambiguity_rules:
        if r.required_action == "clarify" and normalize(r.trigger) == t_norm:
            text, options = r.question_template, list(r.alternatives)
            break
    if text is None:
        for g in ctx.glossary:
            if g.ambiguity_action == "clarify" and normalize(g.term) == t_norm:
                text, options = g.clarification_question_template, list(g.maps_to)
                break
    kind = "airport_or_location" if any(w in t_norm for w in ("airport", "city", "state")) else "scope"
    return ClarificationOutcome(
        outcome="clarification_required", catalog_version=ctx.catalog_version, confidence=1.0,
        clarification=ClarificationDetails(
            question=(text or f"Could you clarify what you mean by '{trigger}'?")[:500],
            ambiguity_type=kind, candidate_options=options[:10]))


def _grounded_topics(ctx: LlmSemanticContext) -> set[str]:
    """Metric labels, selected_domains entries and glossary terms, lower-cased (A7)."""
    topics = {m.label.strip().lower() for m in ctx.metrics}
    topics |= {d.strip().lower() for d in ctx.selected_domains}
    topics |= {g.term.strip().lower() for g in ctx.glossary}
    return topics


def _iso(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def validate_outcome(outcome, ctx: LlmSemanticContext, question: str, options: ParserOptions) -> ValidationResult:
    """C-2.4.1 order. Kind A errors accumulate in `errors`; the first Kind B result is `deterministic`."""
    res = ValidationResult()
    err = res.errors.append
    step1_ok = outcome.catalog_version == ctx.catalog_version
    if not step1_ok:
        err("catalog_version must equal the semantic context catalog_version")

    texts: list[str] = []
    kind_b: tuple[str, list[str], str] | None = None
    if isinstance(outcome, ClarificationOutcome):
        if not options.allow_clarification:
            err("clarification_required is not allowed for this request")
        texts += [outcome.clarification.question, *outcome.clarification.candidate_options]
    elif isinstance(outcome, UnsupportedOutcome):
        if not options.allow_unsupported:
            err("unsupported is not allowed for this request")
        texts += [outcome.unsupported.message, *outcome.unsupported.suggested_supported_topics,
                  *outcome.unsupported.conflicting_metrics]
        allowed_topics = _grounded_topics(ctx)
        for topic in outcome.unsupported.suggested_supported_topics:
            if topic.strip().lower() not in allowed_topics:
                err(f"suggested_supported_topics entry '{topic}' is not a metric label, selected domain "
                    "or glossary term of the context")
        known = {m.name for m in ctx.metrics}
        for name in outcome.unsupported.conflicting_metrics:
            if name not in known:
                err(f"conflicting_metrics entry '{name}' is not a metric of the context")
    elif isinstance(outcome, SemanticRequestOutcome):
        kind_b = _validate_request(outcome, ctx, question, err, texts)

    if any(has_sql_leakage(t) for t in texts):
        res.leakage = True
        err("free-text fields must not contain SQL or code")
    if kind_b is not None and step1_ok and not res.leakage:
        reason, conflicting, message = kind_b
        message = message[:500]
        if not options.allow_unsupported:
            err(message)
        else:
            res.deterministic = UnsupportedOutcome(
                outcome="unsupported", catalog_version=ctx.catalog_version, confidence=1.0,
                unsupported=UnsupportedDetails(message=message, reason=reason, suggested_supported_topics=[],
                                               conflicting_metrics=conflicting))
    return res


def _validate_request(outcome, ctx, question, err, texts):
    """Steps 2-6. Returns the first Kind B result as (reason, conflicting_metrics, message) or None."""
    req = outcome.semantic_request
    by_name = {m.name: m for m in ctx.metrics}
    dims = {d.name: d for d in ctx.dimensions}
    if _clarify_triggered(question, ctx) is not None:
        err("the question matches a clarification trigger in the context; return clarification_required")

    unknown = [n for n in req.metrics if n not in by_name]
    if unknown:
        texts += unknown
        return ("no_approved_metric", [],
                f"These metrics are not available in the approved context: {', '.join(unknown)}.")
    ms = [by_name[n] for n in req.metrics]

    if len(ms) > 1:
        first = ms[0].combinable_with
        if first is None or any(m.combinable_with != first for m in ms[1:]):
            conflicting = [ms[0].name] + [m.name for m in ms[1:]
                                          if m.combinable_with is None or m.combinable_with != first]
            if first is None:
                conflicting = [m.name for m in ms]
            return ("metrics_not_combinable", conflicting,
                    f"These metrics cannot be combined in one query: {', '.join(conflicting)}.")

    def check_dimension(kind: str, d: str):
        """Steps 5a-5c. Returns (ok, kind_b)."""
        if d not in dims:
            err(f"dimension '{d}' is not in the semantic context")
            return False, None
        blocking = [m.name for m in ms if d not in m.allowed_dimensions]
        if len(blocking) == len(ms):
            names = (f"metric '{blocking[0]}'" if len(blocking) == 1
                     else "metrics " + ", ".join(f"'{n}'" for n in blocking))
            err(f"{kind} dimension '{d}' is not allowed for {names}")
            return False, None
        if blocking:
            return False, ("no_supported_dimension", blocking,
                           f"Dimension '{d}' is not available for: {', '.join(blocking)}.")
        return True, None

    for g in req.group_by:
        ok, b = check_dimension("group_by", g.dimension)
        if b is not None:
            return b
        if not ok:
            continue
        if g.time_grain is not None and g.time_grain not in dims[g.dimension].allowed_time_grains:
            err(f"time_grain '{g.time_grain}' is not allowed for dimension '{g.dimension}'")
    for f in req.filters:
        ok, b = check_dimension("filter", f.dimension)
        if b is not None:
            return b
        if not ok:
            continue
        d = dims[f.dimension]
        if f.operator not in d.allowed_operators:
            err(f"operator '{f.operator}' is not allowed for dimension '{f.dimension}'")
        if f.operator == "between" and len(f.values) != 2:
            err("between requires exactly 2 values")
        if f.operator in ("equals", "not_equals") and len(f.values) != 1:
            err(f"{f.operator} requires exactly 1 value")
        texts += f.values
    dr = req.date_range
    if dr is not None:
        if dr.kind == "relative":
            if dr.relative_period is None or dr.start_date or dr.end_date:
                err("a relative date_range needs relative_period and no start_date/end_date")
        else:
            s, e = _iso(dr.start_date), _iso(dr.end_date)
            if dr.relative_period is not None or s is None or e is None or s > e:
                err("an absolute date_range needs valid ISO start_date <= end_date and no relative_period")
    return None
