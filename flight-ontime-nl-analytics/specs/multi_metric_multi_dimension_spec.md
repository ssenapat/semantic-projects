# Spec: Multi-metric and multi-dimension analytics queries

Status: folded into the architecture spec, the two parser API specs and the semantic-layer spec; decisions D1 to D6 and the design decisions are recorded in `specs/ghe/memory/decisions.md` (ADR-001).
Scope: llm-intent-parser-service, semantic-layer, streamlit-analytics-client.
Items marked **[verify]** were not confirmed against the code (parts of `compiler.py` and `response_validator.py` were only partly read). Confirm them before implementing.

## 1. Goal and scope

Answer questions such as these in one request:
- **Several dimensions, one metric:** "cancelled flights last month by carrier and origin airport".
- **Several metrics, one or more dimensions:** "flights, cancellations and cancellation rate last month by carrier".

Out of scope:
- Metrics from different models or grains.
- Cross-model joins.
- Derived metrics invented by the LLM.
- Marketing carrier and other unapproved mappings.

## 2. Current state (summary)

| Layer | Today |
|---|---|
| Parser schema (`ConstrainedSemanticRequest`) | `metric: str` (single). `group_by` is a list, max 8. |
| Parser validator (`response_validator.py`) | Checks every dimension against the single `req.metric`. |
| Semantic-layer compiler | Request takes exactly one of `metric` or `metrics`. `metrics` is a list limited by `MAX_METRICS`. The backend already accepts several metrics. |
| Ambiguity rules | `ROLE_QUALIFIERS` and `ClarificationOutcome` can turn "airport" or "carrier" into a clarification. |
| Client | Built around a single-metric result. **[verify]** |

## 3. Requirements

| ID | Requirement |
|---|---|
| R1 | A request carries 1 to `MAX_METRICS` unique metrics. |
| R2 | A request carries 0 to 8 `group_by` entries. Each may have a time grain. |
| R3 | A single-metric question is expressed as a one-item `metrics` list. The legacy `metric` field is removed (breaking change, see 4.1). |
| R4 | Every group-by and filter dimension must be allowed for every requested metric. |
| R5 | All requested metrics share the same model, grain and `time_basis`. |
| R6 | The LLM only selects catalog items. It never writes SQL. Leakage checks stay on. |
| R7 | The response returns one row per dimension combination, with one value column per metric. |
| R8 | The response is deterministic: same request, same ordering, same truncation. |
| R9 | The result stays read-only and subject to the existing `max_rows`, timeout and audit rules. |
| R10 | Ambiguous terms ("airport", "carrier") produce a clarification question, never a silent guess. |

## 4. Contract changes

### 4.1 Parser request (`app/schemas/response.py`)
```python
class ConstrainedSemanticRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metrics: list[str] = Field(min_length=1, max_length=MAX_METRICS)
    group_by: list[SemanticGroupBy] = Field(default_factory=list, max_length=8)
    filters: list[SemanticFilter] = Field(default_factory=list, max_length=20)
    date_range: SemanticDateRange | None = None
    requested_limit: int = Field(default=500, ge=1, le=5000)
```
- Reject duplicate metrics and duplicate `group_by` (dimension, time_grain) pairs.
- Breaking change (D6): the legacy `metric: str` field is removed with no compatibility alias. Because of `extra="forbid"`, a request or LLM output that still contains `metric` is rejected as a validation error.
- Every producer and consumer of the old field must be updated in the same change: the prompt and few-shot examples, `stub_responses.json`, `fallback_handler.py`, `structured_output_parser.py`, the retriever context fixtures, the client models and the e2e tests. Search the repo for `metric` usages as a checklist item.
- Update the parser API specs (`python_llm_intent_parser_api_specification.md`, `llm_intent_parser_api_specification.md`) and any API version marker. **[verify]** whether the API carries a version that must be bumped.
- `MAX_METRICS` is defined once, and a test asserts the parser and compiler values match. **[verify]** the compiler value.

### 4.2 Parser outcomes
Outcome types are unchanged: `SemanticRequestOutcome`, `ClarificationOutcome`, `UnsupportedOutcome`. Add an optional `conflicting_metrics: list[str]` to explain incompatible combinations.

### 4.3 Semantic-layer request
The parser always sends `metrics` (a list). A single metric becomes a one-item list.

### 4.4 Query response
```json
{
  "columns": [
    {"name": "carrier_code", "role": "dimension"},
    {"name": "origin_airport_code", "role": "dimension"},
    {"name": "flight_count", "role": "metric", "unit": "count", "label": "Flight count"},
    {"name": "cancellation_rate", "role": "metric", "unit": "ratio", "label": "Cancellation rate"}
  ],
  "rows": [["AA", "ORD", 12400, 0.0312]],
  "truncated": false,
  "row_count": 1,
  "metric_versions": {"flight_count": 1, "cancellation_rate": 1}
}
```
- `role` lets the client tell dimension columns from value columns.
- `truncated` is true when `max_rows` or `requested_limit` cut the result.

## 5. Validation rules (`response_validator.py`)

Run in order. Any failure produces a clarification or an unsupported outcome, never a guess.

1. Each metric exists in `ctx.metrics` and has `status: validated`.
2. Metrics are unique.
3. All metrics share the same model, grain and `time_basis`. Otherwise return `unsupported` with reason `metrics_not_combinable`.
4. Compute `allowed` as the intersection of `allowed_dimensions` across all metrics. Every `group_by` and filter dimension must be in `allowed`. If a dimension is allowed for some metrics but not all, name the blocking metrics.
5. `time_grain` must be in the dimension's `allowed_time_grains`.
6. Filter operator and value rules are unchanged (for example `between` takes exactly 2 values).
7. Date range rules are unchanged.
8. Ambiguity (R10): if "airport" or "carrier" matches more than one dimension role, return `ClarificationOutcome` with the matching `ambiguity_type`. **[verify]** that `ROLE_QUALIFIERS` triggers on `group_by` terms, not only on filters.
9. Result size: do not reject large dimension products. Rely on truncation.

## 6. Policy and catalog

- Add `max_metrics_per_query` to `query_policy.yml`, read by both services (single source of truth).
- Add a `combinable_with` group tag to each metric, not pairwise lists. Initially all six `flight_operations` metrics share one group.
- A ratio metric is computed from its own numerator and denominator per group, never from the other columns in the query.
- The `unsupported` list (delay metrics, marketing carrier and so on) is unchanged.
- Record decisions D1 and D2 (section 10) in the glossary and ambiguity rules.

## 7. Semantic-layer compiler (`compiler.py`, `validator.py`)

- One `SELECT` with each measure aliased by metric name, and a `GROUP BY` for each dimension. Identifiers come from the catalog only. User text is bound as parameters.
- All measures share a source and grain, so there is no fan-out or double counting.
- `ORDER BY`: first metric descending, then each dimension ascending (this satisfies R8). **[verify]** how `MAX_ORDER_BY` and existing ordering fit.
- `LIMIT` is `min(requested_limit, max_rows)`. Run `LIMIT n+1` internally so `truncated` can be set.
- Return `metric_versions` per metric.
- The audit record (`semantic_layer.audit`) logs the full metric and dimension lists. **[verify]** that the record keys cover a list of metrics.
- **[verify]** whether ratio and count metrics can be selected together today.

## 8. LLM service

- `prompt_builder.py`: allow a list of metrics and dimensions. Add few-shot examples for compound, clarification and unsupported cases.
- Retriever: return context for every metric mentioned, plus glossary entries for each dimension named.
- `stub_responses.json` and `fallback_handler.py`: add multi-metric entries so stub mode matches live mode.
- `structured_output_parser.py`: parse the `metrics` list form only. A legacy `metric` field in model output is a validation error and goes through the existing fallback or retry path.

## 9. Streamlit client

- `models/api_models.py` and `components/api_client.py`: model the new response (`columns[].role`, `truncated`, `metric_versions`).
- `components/result_view.py`: table with a column per metric, formatted by unit (counts with thousands separators, ratios as percentages).
- `components/charts.py`:
  - One dimension and one metric: bar chart.
  - One dimension and several metrics: grouped bars.
  - Two dimensions: pivot table or grouped chart.
  - More than two dimensions, or a ratio mixed with counts: table.
  - Show a truncation notice when `truncated` is true.
- `pages/3_Metric_Definitions.py`: show allowed dimensions per metric and which metrics combine.
- `pages/2_Query_History.py` and `models/session_models.py`: store and show the metric list. **[verify]** for single-metric assumptions.

## 10. Decisions needed before building

All decisions are settled.

| # | Decision | Outcome |
|---|---|---|
| D1 | Meaning of "airport" when unqualified | Ask a clarification question: origin, destination or both |
| D2 | Meaning of "carrier" | `carrier_code` (operating). Marketing carrier stays unsupported. |
| D3 | `MAX_METRICS` | Keep the compiler's current value |
| D4 | Count with ratio in one query | Allow. Each ratio comes from its own definition. |
| D5 | Behaviour on truncation | Return the rows and set `truncated: true` |
| D6 | Keep legacy `metric` input | **No.** Remove it. The `metrics` list is the only form. |

## 11. Test plan

**Parser unit tests**
- A single metric as a one-item `metrics` list is accepted.
- A request containing the legacy `metric` field is rejected.
- Several metrics are accepted. Duplicates are rejected.
- A dimension that one metric doesn't allow is rejected, naming the metric.
- Ambiguous "airport" gives a clarification.
- Unsupported terms give an unsupported outcome.

**Compiler tests** (`semantic-layer/tests/test_compiler.py`)
- Expected SQL for 2 metrics with 1 dimension, 1 metric with 2 dimensions, and 2 metrics with 2 dimensions.
- Deterministic ordering.
- `LIMIT` and `truncated`.

**Integration tests** (`semantic-layer/tests/test_integration_db.py`)
- Each metric's total equals the single-metric result (reconcile with `validation/metric_reconciliation.sql`).
- A grouped result sums to the ungrouped total, apart from truncation.

**Fixtures** (`llm-intent-parser-service/tests/fixtures/retriever_contexts/`)
- One per acceptance example below. Re-run `tests/snapshot_retriever_contexts.py`.

**End-to-end** (`e2e/evaluation/test_evaluation.py`)
- Add the acceptance examples and the rejected cases.

## 12. Acceptance examples

1. "Cancelled flights last month by carrier and origin airport" returns a table with columns `carrier_code`, `origin_airport_code`, `cancelled_flight_count`.
2. "Flights, cancellations and cancellation rate last month by carrier" returns one row per carrier with three value columns.
3. "Flights and cancelled flights last month by carrier and origin airport, only for Chicago" returns the combined case with the filter applied.
4. "Cancelled flights by airport" returns a clarification question.
5. "Cancelled flights by airline name" returns an unsupported outcome.
6. A single-metric question, now sent as a one-item `metrics` list, gives the same answer as before the change.
7. A request using the removed `metric` field is rejected with a validation error.

Request examples:

```json
{
  "metrics": ["cancelled_flight_count"],
  "group_by": [{"dimension": "carrier_code"}, {"dimension": "origin_airport_code"}],
  "filters": [],
  "date_range": {"kind": "relative", "relative_period": "last_complete_calendar_month"},
  "requested_limit": 500
}
```

```json
{
  "metrics": ["flight_count", "cancelled_flight_count", "cancellation_rate"],
  "group_by": [{"dimension": "carrier_code"}],
  "filters": [],
  "date_range": {"kind": "relative", "relative_period": "last_complete_calendar_month"},
  "requested_limit": 500
}
```

## 13. Suggested work breakdown (for Jira)

| Story | Area | Depends on |
|---|---|---|
| S0 | Resolve the **[verify]** items (decisions D1 to D6 are settled) | none |
| S1 | Contract: replace `metric` with `metrics` (no alias), update every usage of the old field, `MAX_METRICS` in one place, `max_metrics_per_query` policy | S0 |
| S2 | Validator rules and parser unit tests | S1 |
| S3 | Compiler: multi-metric SQL, ordering, truncation, audit, tests | S1 |
| S4 | Prompt, retriever, stubs and fallbacks | S1, S2 |
| S5 | Client: models, result view, charts, definitions and history pages | S1, S3 |
| S6 | Fixtures, e2e evaluation, integration reconciliation | S2, S3, S4, S5 |
| S7 | Fold this spec into the three existing specs and add an ADR to `specs/ghe/memory/decisions.md` | S6 |
