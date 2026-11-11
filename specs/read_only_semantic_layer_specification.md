# Read-Only Airline Semantic Layer Specification for Amazon Redshift

## 1. Purpose

This specification defines a **metadata-driven, read-only semantic layer** for an existing Amazon Redshift database containing airline-industry data.

The organization has read-only database access. Therefore, this implementation does **not** create or modify any Redshift tables, views, schemas, materialized views, roles, grants, procedures, or other database objects.

The semantic layer is maintained externally as version-controlled metadata, documentation, validation SQL, and—later—a restricted Python query compiler/service. It translates physical Redshift objects into approved business concepts such as flight operations, schedules, passenger journeys, revenue, loyalty, baggage, maintenance, or workforce activity only when the actual available data supports those concepts.

This phase intentionally excludes the Streamlit application. A future Streamlit application will consume this semantic layer as a thin presentation client.

---

## 2. Objectives

Create a semantic layer that:

- Defines a governed business vocabulary over existing Redshift data.
- Documents source tables, columns, grain, keys, timestamp semantics, carrier roles, status values, data freshness, and sensitivity.
- Defines reusable entities, dimensions, relationships, measures, and metrics outside the database.
- Stores all business meaning and metric logic in Git-managed YAML, JSON, Markdown, Python, and SQL files.
- Enables future applications to generate safe, parameterized, read-only `SELECT` queries.
- Prevents downstream applications from embedding inconsistent metric formulas, arbitrary joins, or raw SQL logic.
- Enforces a default-deny policy for sensitive passenger, employee, crew, payment, and security-related data.
- Supports an auditable lifecycle for draft, approved, deprecated, and versioned metric definitions.

---

## 3. Scope

### In scope

- Read-only Redshift source discovery.
- Metadata-driven source inventory.
- Airline business glossary.
- Semantic model definitions.
- Entity, dimension, relationship, measure, and metric definitions.
- Read-only profiling, validation, and reconciliation SQL.
- Semantic-definition schema validation.
- Data classification and access-policy configuration.
- Git-based review, versioning, and release process.
- Future-ready Python query compiler/service design.

### Out of scope

- Creating Redshift schemas, tables, views, materialized views, or stored procedures.
- Modifying source data.
- Creating Redshift roles or permissions.
- dbt models that materialize data into Redshift.
- Streamlit UI development.
- Dashboards, embedded analytics, or reporting UX.
- LLM-driven text-to-SQL.
- Data ingestion, ETL, or source-system redesign.
- Predictive analytics, optimization, forecasting, or machine learning.
- External user access.

---

## 4. Constraint: Read-Only Access

The Redshift connection must be used only for discovery, validation, reconciliation, and future governed data retrieval.

Permitted SQL operations:

```sql
SELECT
```

Potentially permitted metadata reads, depending on database grants:

```sql
SELECT ... FROM information_schema.tables;
SELECT ... FROM information_schema.columns;
SELECT ... FROM pg_catalog...;
```

The following operations are prohibited for this project phase:

```sql
CREATE SCHEMA
CREATE TABLE
CREATE VIEW
CREATE MATERIALIZED VIEW
ALTER
INSERT
UPDATE
DELETE
DROP
GRANT
REVOKE
CALL
```

No database DDL or DML is required to define the semantic layer.

---

## 5. Target Architecture

```text
                         Git Repository
┌──────────────────────────────────────────────────────────────┐
│ Airline semantic definitions                                  │
│                                                              │
│ - Source inventory                                           │
│ - Business glossary                                          │
│ - Semantic models                                            │
│ - Metrics and dimensions                                     │
│ - Relationship definitions                                   │
│ - Data policies                                              │
│ - Read-only validation SQL                                   │
│ - Query templates / future query compiler                    │
└────────────────────────────┬─────────────────────────────────┘
                             │
                             │ Loads definitions and compiles
                             │ approved parameterized SELECT SQL
                             ▼
                 Future Python semantic-query service
                             │
                             │ Read-only connection
                             ▼
┌──────────────────────────────────────────────────────────────┐
│ Existing Amazon Redshift database                             │
│                                                              │
│ Existing schemas, tables, and views only                     │
│ No new database objects are created                          │
└──────────────────────────────────────────────────────────────┘
                             │
                             ▼
                 Future Streamlit application
```

### Architectural rule

The semantic layer is the source of truth for business definitions. Redshift remains the source of data and query execution engine.

A future Streamlit application must not contain canonical metric calculations, unrestricted table selection, arbitrary joins, or user-supplied raw SQL.

---

## 6. Semantic Layer Components

| Component | Purpose | Storage location |
|---|---|---|
| Source inventory | Documents accessible Redshift objects and source assumptions | Git Markdown/YAML |
| Business glossary | Defines airline business terms and ambiguity decisions | Git Markdown |
| Source definitions | Maps approved physical tables and columns | Git YAML |
| Semantic models | Defines business entities, dimensions, and measures | Git YAML |
| Metric definitions | Defines KPI formulas, filters, time basis, and allowed dimensions | Git YAML |
| Relationship definitions | Defines allowed joins and cardinality | Git YAML |
| Data policies | Defines allowed sources, fields, PII restrictions, and scope rules | Git YAML/JSON |
| Discovery SQL | Read-only SQL used to understand data | Git SQL |
| Validation SQL | Read-only SQL used to test assumptions | Git SQL |
| Reconciliation SQL | Compares governed metrics with approved reference calculations | Git SQL |
| Query compiler | Later Python component that produces safe parameterized SQL | Git Python |
| Test fixtures | Approved reference cases and expected query behavior | Git YAML/CSV |

---

## 7. Repository Structure

Create a repository named, for example:

```text
airline-semantic-layer/
├── README.md
├── pyproject.toml
├── semantic_layer/
│   ├── __init__.py
│   ├── models.py
│   ├── registry.py
│   ├── validator.py
│   ├── policy.py
│   ├── query_compiler.py
│   └── redshift_client.py
├── definitions/
│   ├── sources/
│   │   └── redshift_sources.yml
│   ├── semantic_models/
│   │   ├── flight_operations.yml
│   │   ├── commercial.yml
│   │   ├── passenger_journey.yml
│   │   ├── loyalty.yml
│   │   ├── baggage.yml
│   │   ├── maintenance.yml
│   │   └── workforce.yml
│   ├── metrics/
│   │   ├── flight_operations_metrics.yml
│   │   ├── commercial_metrics.yml
│   │   ├── loyalty_metrics.yml
│   │   ├── maintenance_metrics.yml
│   │   └── passenger_experience_metrics.yml
│   ├── relationships/
│   │   └── airline_relationships.yml
│   ├── policies/
│   │   ├── allowed_sources.yml
│   │   ├── pii_policy.yml
│   │   ├── organization_scope.yml
│   │   └── query_policy.yml
│   └── mappings/
│       ├── status_mappings.yml
│       ├── airport_code_policy.yml
│       ├── carrier_role_policy.yml
│       └── time_semantics.yml
├── docs/
│   ├── source-data-inventory.md
│   ├── airline-business-glossary.md
│   ├── metric-catalog.md
│   ├── data-classification.md
│   ├── query-contract.md
│   └── change-management.md
├── sql/
│   ├── discovery/
│   │   ├── list_tables.sql
│   │   ├── list_columns.sql
│   │   ├── profile_candidate_keys.sql
│   │   ├── profile_categorical_values.sql
│   │   └── profile_timestamps.sql
│   ├── validation/
│   │   ├── grain_check.sql
│   │   ├── key_uniqueness_check.sql
│   │   ├── status_value_check.sql
│   │   ├── timestamp_coverage_check.sql
│   │   ├── airport_code_check.sql
│   │   ├── carrier_role_check.sql
│   │   └── metric_reconciliation.sql
│   └── templates/
│       ├── metric_query.sql.j2
│       └── flight_operations.sql.j2
├── tests/
│   ├── test_definition_validation.py
│   ├── test_metric_compilation.py
│   ├── test_policy_enforcement.py
│   ├── test_query_safety.py
│   └── fixtures/
│       └── approved_metric_cases.yml
└── .github/
    └── workflows/
        └── validate.yml
```

The domain files are placeholders. Create and approve a domain only after confirming that corresponding accessible source data exists.

---

## 8. Source Discovery Specification

No semantic model may be approved before its underlying data is inventoried.

Create the following documentation first:

```text
docs/source-data-inventory.md
docs/airline-business-glossary.md
docs/data-classification.md
```

### 8.1 Required source inventory fields

For each accessible Redshift table or view that may support airline analytics, record:

| Field | Required content |
|---|---|
| Source system | Reservation, ticketing, flight operations, departure control, loyalty, finance, baggage, crew, maintenance, airport/vendor feed, or unknown |
| Redshift object | Fully qualified schema and table/view name |
| Business owner | Business department accountable for meaning |
| Technical owner | Team accountable for source/ingestion |
| Source purpose | Known or hypothesized purpose |
| Row grain | What one row represents; mark as `unknown` until confirmed |
| Candidate primary key | Single or composite candidate key |
| Candidate organization key | Airline, carrier, subsidiary, brand, partner, or business-unit identifier |
| Flight identifiers | Flight number, leg key, segment key, schedule key, movement key, or operation key if present |
| Timestamp columns | Exact column names and known/unknown semantics |
| Time-zone basis | UTC, local, source system time, or unknown |
| Status columns | Column names and observed values |
| Carrier role columns | Operating carrier, marketing carrier, codeshare indicator, or unknown |
| Airport/route columns | Airport code type and origin/destination meaning |
| Passenger/crew/employee fields | PII or sensitive-data classification |
| Financial fields | Currency, amount, refund, recognition semantics, or unknown |
| Data freshness | Expected update frequency and latency |
| Data corrections | Backfill, cancellation, restatement, or late-arriving behavior |
| Candidate dimensions | Potential approved grouping/filter fields |
| Candidate measures | Potential numeric/countable values |
| Known caveats | Duplicates, nulls, code quality, ambiguous definitions, missing keys |

### 8.2 Required discovery SQL

List accessible tables and views:

```sql
SELECT
    table_schema,
    table_name,
    table_type
FROM information_schema.tables
WHERE table_schema NOT IN (
    'information_schema',
    'pg_catalog',
    'pg_internal'
)
ORDER BY
    table_schema,
    table_name;
```

Inspect columns:

```sql
SELECT
    table_schema,
    table_name,
    ordinal_position,
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_schema = '<schema_name>'
  AND table_name = '<table_name>'
ORDER BY ordinal_position;
```

Profile a candidate primary key:

```sql
SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT <candidate_key>) AS distinct_candidate_keys,
    SUM(
        CASE
            WHEN <candidate_key> IS NULL THEN 1
            ELSE 0
        END
    ) AS null_candidate_keys
FROM <schema_name>.<table_name>;
```

Profile a categorical/status field:

```sql
SELECT
    <candidate_status_column> AS value,
    COUNT(*) AS row_count
FROM <schema_name>.<table_name>
GROUP BY <candidate_status_column>
ORDER BY row_count DESC;
```

Profile timestamp coverage:

```sql
SELECT
    MIN(<candidate_timestamp>) AS earliest_timestamp,
    MAX(<candidate_timestamp>) AS latest_timestamp,
    SUM(
        CASE
            WHEN <candidate_timestamp> IS NULL THEN 1
            ELSE 0
        END
    ) AS null_timestamp_count,
    COUNT(*) AS total_rows
FROM <schema_name>.<table_name>;
```

All discovery SQL must be committed to Git and run only as read-only `SELECT` statements.

---

## 9. Airline-Specific Interpretation Rules

Airline data contains domain-specific ambiguities. The semantic layer must record uncertainty rather than silently choosing an interpretation.

The following must be confirmed before publishing a related metric.

| Topic | Required clarification |
|---|---|
| Flight grain | Whether a row represents a schedule, flight number, leg, segment, movement, operation, or itinerary segment |
| Carrier role | Whether a carrier is operating, marketing, codeshare, partner, or unknown |
| Airport code | Whether codes are IATA, ICAO, internal station codes, or mixed |
| Timestamp type | Whether timestamp is scheduled, estimated, actual, reported, finalized, issue time, travel time, or accounting time |
| Time zone | Whether time is UTC, origin local time, destination local time, source-system local time, or unknown |
| Cancellation | Whether cancellation refers to flight operation, booking, itinerary, ticket, refund, or service event |
| Passenger meaning | Whether passenger means booked, checked-in, boarded, flown, revenue passenger, or unique customer |
| Revenue meaning | Whether revenue is gross sales, net of refunds, recognized revenue, ancillary revenue, prorated revenue, or another finance measure |
| Aircraft meaning | Whether aircraft refers to tail number, aircraft type, fleet family, planned equipment, or operated equipment |
| Status value | How each source status maps to an approved business status |
| Organizational scope | Whether data belongs to one airline, multiple brands, subsidiaries, operating certificates, partners, or customers |

No metric may be promoted from `draft` to `approved` while a required interpretation remains unresolved.

---

## 10. Semantic Modeling Standard

### 10.1 Semantic concepts

| Concept | Meaning |
|---|---|
| Source | A physical Redshift table or view that can be queried read-only |
| Semantic model | A governed mapping from source fields to business concepts |
| Entity | A business object or stable join key, such as flight operation, airport, aircraft, booking, or loyalty member |
| Dimension | A non-aggregated attribute allowed for filtering or grouping, such as date, airport, carrier, fleet type, or status |
| Measure | A base aggregation, such as count, distinct count, sum, average, minimum, or maximum |
| Metric | A user-facing KPI derived from one or more approved measures and rules |
| Relationship | A documented, allowed join path between approved entities |
| Policy | Rules governing permitted sources, columns, filters, scopes, and query behavior |

### 10.2 Definition states

| Status | Meaning |
|---|---|
| `discovered` | Object/field found but not yet modeled |
| `draft` | Proposed semantic definition; not safe for consumption |
| `validated` | Technical checks passed; awaiting business approval or final reconciliation |
| `approved` | Published for governed use |
| `deprecated` | Still available temporarily; replacement and migration path documented |
| `retired` | No longer available for new use |

### 10.3 Naming standards

| Object | Standard | Example |
|---|---|---|
| Source name | Descriptive snake case | `flight_movements_source` |
| Semantic model | Singular domain name | `flight_operations` |
| Entity | Singular noun | `flight_operation`, `airport`, `aircraft` |
| Dimension | Business-friendly snake case | `operation_status`, `origin_airport` |
| Measure | Aggregation-oriented snake case | `operated_flights`, `departure_delay_minutes` |
| Metric | User-facing KPI snake case | `on_time_departure_rate` |
| Versioned metric | Append version suffix | `on_time_departure_rate_v2` |

### 10.4 Mandatory definition attributes

Every approved metric must include:

- Technical metric name and business label.
- Status and semantic version.
- Business owner and technical owner.
- Source model and physical source object.
- Fact grain and primary entity.
- Exact aggregation/formula.
- Inclusion and exclusion criteria.
- Required filters and allowed dimensions.
- Time basis, time zone, and supported time grains.
- Organization/carrier scope behavior.
- Data classification level.
- Data freshness expectation.
- Validation and reconciliation SQL reference.
- Effective date and change log.

---

## 11. Airline Domain Framework

The following domains are possible semantic-layer domains. Do not implement any domain until its source data is discovered and validated.

| Domain | Potential semantic model | Typical fact grain | Candidate entities | Candidate dimensions | Candidate measures |
|---|---|---|---|---|---|
| Flight operations | `flight_operations` | One operation, movement, leg, or finalized operational record | flight operation, aircraft, airport, carrier, route | operation date, status, origin, destination, fleet type | operated flights, delay minutes, cancelled flights |
| Network and schedules | `flight_schedule` | One scheduled flight leg per date/version | scheduled flight, route, carrier, airport | schedule date, departure bank, route, service type | scheduled flights, scheduled seats |
| Reservations and bookings | `booking_activity` | One booking, booking segment, or transaction | booking, itinerary, passenger journey, segment | booking date, channel, fare family, cabin | booking count, booked segments, booking amount |
| Passenger journey | `passenger_journey` | One passenger segment or itinerary leg | passenger journey, booking, flight segment | travel date, cabin, board status, disruption type | boarded passengers, no-shows, connections |
| Ticketing and revenue | `ticket_revenue` | One ticket coupon, transaction, or recognition record | ticket, coupon, payment, flight segment | issue date, flown date, currency, fare class | gross amount, refunds, recognized revenue |
| Ancillary services | `ancillary_activity` | One ancillary transaction | ancillary sale, passenger journey, segment | ancillary type, channel, service date | units sold, ancillary revenue |
| Loyalty | `loyalty_activity` | One loyalty transaction/activity | loyalty member, loyalty transaction | tier, activity date, transaction type | miles earned, miles redeemed, active members |
| Baggage | `baggage_events` | One bag event or bag journey | bag, station, passenger journey, segment | event date, station, event type, status | bags handled, delayed bags, mishandled bags |
| Maintenance | `maintenance_activity` | One work order, task, defect, or event | aircraft, work order, maintenance event | date, aircraft type, ATA chapter, station | event count, downtime, labor hours |
| Crew/workforce | `crew_activity` | One crew assignment, duty period, or roster event | crew member, duty period, flight operation | duty date, role, base, qualification | assigned crew, duty hours, disruptions |
| Customer service | `service_case_activity` | One case or interaction | case, passenger journey, channel | date, reason, channel, status | cases opened, resolution time, compensation |

---

## 12. Generic Source Definition Template

Use this template for each discovered source. Replace bracketed fields only after inspection and validation.

```yaml
# definitions/sources/redshift_sources.yml

sources:
  - name: flight_movements_source
    status: discovered
    connection: redshift_readonly
    physical_object: "<schema_name>.<table_or_view_name>"
    description: >
      Candidate airline operational source. The grain and business meaning
      are pending validation.

    access:
      mode: read_only
      allowed_operations:
        - SELECT

    ownership:
      business_owner: "<team_or_role>"
      technical_owner: "<team_or_role>"

    grain:
      status: unknown
      description: >
        Pending confirmation. Possible interpretations include a flight schedule,
        flight leg, operational movement, or another source-system record.

    source_freshness:
      expected_frequency: "<unknown_or_interval>"
      late_arriving_data_policy: "<unknown_or_documented_policy>"

    organization_scope:
      physical_column: "<organization_or_carrier_key>"
      status: draft

    columns:
      - semantic_name: flight_operation_id
        physical_column: "<candidate_key_column>"
        role: candidate_primary_key
        classification: internal
        validation:
          - not_null
          - uniqueness

      - semantic_name: operation_timestamp
        physical_column: "<candidate_timestamp_column>"
        role: candidate_time
        classification: internal
        time_semantics: unknown

      - semantic_name: operation_status
        physical_column: "<candidate_status_column>"
        role: candidate_dimension
        classification: internal
        validation:
          - approved_status_mapping
```

---

## 13. Generic Semantic Model Template

```yaml
# definitions/semantic_models/flight_operations.yml

semantic_model:
  name: flight_operations
  label: Flight Operations
  status: draft
  version: 0.1.0

  ownership:
    business_owner: "<operations_analytics_owner>"
    technical_owner: "<data_platform_owner>"

  source:
    name: "<approved_source_name>"
    physical_object: "<schema_name>.<table_or_view_name>"

  grain:
    status: unapproved
    description: >
      One row per [TBD: scheduled flight, flight leg, flight operation,
      movement, or other validated unit].

  entities:
    - name: flight_operation
      label: Flight Operation
      status: draft
      entity_type: primary
      physical_column: "<candidate_primary_key>"
      validation:
        - not_null
        - uniqueness

    - name: organization
      label: Organization
      status: draft
      entity_type: foreign
      physical_column: "<organization_key>"
      validation:
        - not_null

    - name: operating_carrier
      label: Operating Carrier
      status: draft
      entity_type: foreign
      physical_column: "<operating_carrier_key>"
      validation:
        - code_mapping

  dimensions:
    - name: operation_time
      label: Operation Time
      status: draft
      type: time
      physical_column: "<approved_timestamp_column>"
      time_semantics: >
        TBD: scheduled/estimated/actual and departure/arrival meaning;
        UTC/local time-zone basis must be approved.
      allowed_grains:
        - day
        - week
        - month

    - name: operation_status
      label: Operation Status
      status: draft
      type: categorical
      physical_column: "<status_column>"
      validation:
        - approved_status_mapping

    - name: origin_airport
      label: Origin Airport
      status: draft
      type: categorical
      physical_column: "<origin_airport_column>"
      validation:
        - airport_code_type
        - airport_reference_mapping

    - name: destination_airport
      label: Destination Airport
      status: draft
      type: categorical
      physical_column: "<destination_airport_column>"
      validation:
        - airport_code_type
        - airport_reference_mapping

  measures:
    - name: flight_operation_count_base
      label: Flight Operation Count Base
      status: draft
      aggregation: count_distinct
      expression: "<candidate_primary_key>"
      description: >
        Distinct count of the validated flight-operation primary entity.
```

---

## 14. Generic Metric Definition Template

```yaml
# definitions/metrics/flight_operations_metrics.yml

metrics:
  - name: flight_operation_count
    label: Flight Operation Count
    status: draft
    version: 0.1.0

    ownership:
      business_owner: "<operations_analytics_owner>"
      technical_owner: "<data_platform_owner>"

    source_model: flight_operations
    source_entity: flight_operation

    aggregation:
      type: count_distinct
      expression: "<candidate_primary_key>"

    business_definition: >
      Count of distinct validated flight-operation records at the approved fact grain.

    filters:
      required: []
      included: []
      excluded: []

    dimensions_allowed:
      - operation_time
      - operation_status
      - operating_carrier
      - origin_airport
      - destination_airport

    time_basis:
      dimension: operation_time
      semantics: unapproved
      timezone: unapproved
      allowed_grains:
        - day
        - week
        - month

    organization_scope:
      required: true
      entity: organization
      enforcement: future_query_service

    data_classification: internal

    validation:
      required:
        - Confirm fact grain
        - Confirm primary-key uniqueness
        - Confirm timestamp semantics
        - Confirm organization scope
        - Confirm carrier role
        - Reconcile with approved reference SQL

    lifecycle:
      effective_date: null
      deprecated_by: null
      change_log: []
```

---

## 15. Metric Catalog Standard

Create `docs/metric-catalog.md`. Every metric must use this template.

```markdown
## <metric_name>

- Label: <Business-friendly label>
- Status: Draft | Validated | Approved | Deprecated | Retired
- Version: <semantic version>
- Business owner: <Team/role>
- Technical owner: <Team/role>
- Domain: <Domain name>
- Definition: <Exact business definition>
- Fact grain: <What one source row represents>
- Primary entity: <Entity name>
- Source: <schema.table or view>
- Semantic model: <Semantic model name>
- Formula: <Exact aggregation or formula>
- Included records: <Rules>
- Excluded records: <Rules>
- Time basis: <Timestamp, scheduled/actual/etc., UTC/local>
- Supported time grains: <Day/week/month/etc.>
- Carrier basis: <Operating/marketing/other/not applicable>
- Organization scope: <Required scope behavior>
- Allowed dimensions: <List>
- Data classification: <Internal/confidential/restricted>
- Freshness expectation: <SLA or unknown>
- Validation SQL: <Repository path>
- Reconciliation status: <Pending/passed/date>
- Effective date: <Date>
- Change log: <Versioned changes>
```

---

## 16. Relationship Definition Standard

Relationships must be defined outside the database and used only after evidence supports the join path.

```yaml
# definitions/relationships/airline_relationships.yml

relationships:
  - name: flight_operation_to_airport
    status: draft
    left_model: flight_operations
    left_entity: origin_airport
    right_model: airports
    right_entity: airport
    join_type: many_to_one
    join_condition:
      left_column: "<origin_airport_key>"
      right_column: "<airport_key>"
    validation:
      - referential_integrity
      - airport_code_mapping
    notes: >
      Do not enable until airport code system and historical effective-dating
      requirements are documented.
```

### Relationship rules

- No unrestricted, automatic, or LLM-invented joins.
- Every join must have documented left/right entities and expected cardinality.
- Many-to-many joins require an explicit bridge model and business approval.
- Fact-to-fact joins are prohibited unless an approved join strategy documents the grain and double-counting prevention logic.
- Operational and financial facts must not be joined without an approved reconciliation policy.

---

## 17. Data Policy Specification

### 17.1 Default-deny sensitive-data policy

Create `definitions/policies/pii_policy.yml`.

```yaml
policy:
  default_access: deny

  prohibited_in_standard_semantic_layer:
    - passenger_name
    - passenger_email
    - passenger_phone
    - passenger_address
    - passport_number
    - visa_information
    - government_id
    - payment_card_data
    - authentication_secrets
    - employee_personal_data
    - crew_personal_data
    - security_incident_details

  permitted_with_classification_and_approval:
    - surrogate_passenger_identifier
    - surrogate_loyalty_member_identifier
    - ticket_amount
    - revenue_amount
    - employee_or_crew_surrogate_identifier
    - aircraft_maintenance_detail

  rules:
    - Never expose direct PII in standard metric or dimension definitions.
    - Use surrogate identifiers when an entity relationship is needed.
    - Require a documented business purpose for restricted fields.
    - Require explicit policy approval before a restricted field becomes queryable.
```

### 17.2 Organization-scope policy

If data can include multiple airlines, subsidiaries, brands, operating certificates, partners, or business units, define an organization scope policy.

```yaml
# definitions/policies/organization_scope.yml

organization_scope:
  status: draft
  required_for_tenant_scoped_metrics: true

  approved_scope_entities:
    - organization
    - airline
    - operating_company
    - business_unit

  enforcement:
    phase_current: documentation_and_validation
    phase_future: semantic_query_service

  rule: >
    Future consumer applications must derive authorized organizational scope
    from trusted server-side identity context. Scope must not depend solely
    on browser-provided or user-selected filter values.
```

### 17.3 Query policy

```yaml
# definitions/policies/query_policy.yml

query_policy:
  database_access_mode: read_only
  allowed_statement_types:
    - SELECT

  prohibited_statement_types:
    - INSERT
    - UPDATE
    - DELETE
    - MERGE
    - CREATE
    - ALTER
    - DROP
    - GRANT
    - REVOKE
    - CALL

  query_controls:
    require_approved_metric: true
    require_allowlisted_source: true
    require_allowlisted_dimensions: true
    require_parameterized_values: true
    prohibit_raw_user_sql: true
    prohibit_raw_llm_sql: true
    require_organization_scope_when_applicable: true
    limit_maximum_date_range: true
    limit_maximum_result_rows: true
```

---

## 18. Future Query Compiler Specification

This component is not required for the first metadata-only release. It is the planned bridge between the semantic layer and a future Streamlit app.

### 18.1 Input contract

The query service must accept a structured request, never raw SQL.

```python
metric_request = {
    "metric": "flight_operation_count",
    "group_by": [
        "origin_airport",
        "operation_time__month"
    ],
    "filters": {
        "operation_status": ["operated"]
    },
    "date_range": {
        "start": "2026-07-01",
        "end": "2026-07-31"
    },
    "organization_scope": ["authorized_organization_id"]
}
```

### 18.2 Required compiler behavior

The future query compiler must:

1. Load the semantic definitions from an approved versioned release.
2. Verify the requested metric has `status: approved`.
3. Resolve the metric to allowlisted source objects and fields.
4. Validate requested dimensions against `dimensions_allowed`.
5. Validate filters against approved fields and mapped categorical values.
6. Apply mandatory organization scope where applicable.
7. Apply the metric’s approved time basis, time zone, carrier-role policy, and exclusion criteria.
8. Build parameterized SQL using a safe SQL library or controlled templates.
9. Execute only a `SELECT` query through a read-only Redshift credential.
10. Enforce date-range, row-count, query-time, and result-size limits.
11. Log the semantic metric/version, caller identity, scope, dimensions, filters, execution time, and row count.
12. Avoid logging direct PII, query-result payloads containing sensitive data, passwords, or connection strings.

### 18.3 Example compiled SQL shape

This is illustrative only. Physical names must come from approved definitions, never from user input.

```sql
SELECT
    origin_airport_code AS origin_airport,
    DATE_TRUNC('month', operation_timestamp) AS operation_month,
    COUNT(DISTINCT flight_operation_id) AS flight_operation_count
FROM <approved_schema>.<approved_table>
WHERE operation_timestamp >= %s
  AND operation_timestamp < %s
  AND organization_id = ANY(%s)
  AND standardized_operation_status = ANY(%s)
GROUP BY
    origin_airport_code,
    DATE_TRUNC('month', operation_timestamp)
ORDER BY
    operation_month,
    origin_airport;
```

---

## 19. Validation and Reconciliation

### 19.1 Required source validation

Before promoting a semantic model beyond `draft`, validate:

- Candidate primary-key completeness and uniqueness.
- Source fact grain.
- Duplicate and correction behavior.
- Required timestamp completeness.
- Timestamp semantics and time-zone basis.
- Status values and mappings.
- Organization/carrier scope.
- Airport code system and mappings, where relevant.
- Carrier role semantics: operating versus marketing/codeshare.
- Data freshness and late-arriving records.
- Sensitive-data classification.

### 19.2 Generic validation SQL template

```sql
SELECT
    COUNT(*) AS total_rows,
    COUNT(DISTINCT <candidate_primary_key>) AS distinct_key_count,
    SUM(CASE WHEN <candidate_primary_key> IS NULL THEN 1 ELSE 0 END) AS null_key_count,
    SUM(CASE WHEN <candidate_timestamp> IS NULL THEN 1 ELSE 0 END) AS null_timestamp_count
FROM <schema_name>.<table_name>;
```

### 19.3 Metric reconciliation requirement

Every metric must have a direct read-only reference query before approval.

Reconciliation process:

1. Pick at least three fixed date ranges.
2. Include at least one operationally unusual period if known and permitted.
3. Write a direct, source-traceable SQL reference query.
4. Generate the equivalent SQL through the semantic definition or query compiler.
5. Compare results by the approved dimensions.
6. Document and approve any expected difference.
7. Fail release validation for unexplained differences.

Generic reference query:

```sql
SELECT
    DATE_TRUNC('month', <approved_timestamp_column>) AS month,
    COUNT(DISTINCT <approved_primary_key>) AS metric_value
FROM <approved_schema>.<approved_table>
WHERE <approved_timestamp_column> >= '<start_date>'
  AND <approved_timestamp_column> < '<end_date>'
  AND <approved_inclusion_condition>
GROUP BY 1
ORDER BY 1;
```

---

## 20. Flight Operations Example Framework

Use this framework only if discovery identifies a reliable operational-flight source. It is a template, not an assumption about available data.

### Candidate semantic model

```text
Semantic model: flight_operations
Fact grain: One row per [validated operational unit]
Source: [validated Redshift source]
```

### Candidate entities

| Entity | Required validation |
|---|---|
| Flight operation | Stable key, non-null, unique at documented grain |
| Organization | Airline/subsidiary/business-unit scope behavior |
| Operating carrier | Confirm code format and role |
| Marketing carrier | Confirm code format and codeshare behavior if available |
| Origin airport | Confirm code system and mapping |
| Destination airport | Confirm code system and mapping |
| Aircraft | Confirm tail/equipment identity if available |
| Aircraft type | Confirm fleet/equipment mapping if available |
| Route | Define origin-destination and directional behavior if modeled |

### Candidate dimensions

| Dimension | Approval requirement |
|---|---|
| Operation date/time | Exact scheduled/estimated/actual and UTC/local interpretation |
| Operation status | Approved source-to-standard mapping |
| Origin airport | Airport code system and mapping |
| Destination airport | Airport code system and mapping |
| Operating carrier | Carrier-role policy |
| Marketing carrier | Codeshare policy |
| Fleet type | Equipment mapping |
| Service type | Confirm classification source and values |
| Cancellation reason | Confirm source/normalization rules |
| Diversion indicator | Confirm operational interpretation |

### Candidate metrics

| Metric | Required clarification before approval |
|---|---|
| Flight operation count | Fact grain and unique operation identifier |
| Operated flight count | Exact operated-status list |
| Cancelled flight count | Exact cancelled-status list and denominator context |
| Cancellation rate | Numerator, denominator, timing basis, diversion treatment |
| On-time departure rate | Threshold, scheduled/actual time, exclusions, operating/marketing basis |
| On-time arrival rate | Threshold, scheduled/actual time, exclusions, operating/marketing basis |
| Average departure delay | Delay formula, null policy, cancellation/diversion inclusion |
| Average arrival delay | Delay formula, null policy, cancellation/diversion inclusion |
| Flown block hours | Actual-time source and rounding rules |
| Load factor | Approved passenger and available-seat definitions |

Do not publish these metrics simply because columns appear to exist. Every definition must be technically validated and business-approved.

---

## 21. Versioning and Change Management

### 21.1 Breaking semantic changes

Treat each of the following as a breaking change:

- Changing a metric formula.
- Changing the underlying fact grain.
- Changing operating-carrier versus marketing-carrier basis.
- Changing scheduled versus actual time basis.
- Changing UTC versus local-time reporting basis.
- Changing cancellation, diversion, delay, or status inclusion logic.
- Changing revenue recognition, refund, or currency conversion treatment.
- Renaming or removing a metric, entity, dimension, source, or relationship.
- Changing organization-scope enforcement logic.

### 21.2 Required versioning process

1. Create a new version, such as `on_time_departure_rate_v2`.
2. Keep the old metric available with `status: deprecated` for the agreed migration period.
3. Document old and new definitions, effective date, and expected output impact.
4. Reconcile both versions during the migration period.
5. Update future consumers to use the new metric.
6. Retire the prior version only after downstream migration is complete.

### 21.3 Pull request requirements

Every semantic-definition change must include:

- Updated YAML/JSON definition.
- Updated business glossary and metric catalog.
- Updated validation or reconciliation SQL when relevant.
- Test coverage for schema and policy validation.
- Business-owner approval for changed metric logic.
- Security review when new fields may be sensitive.

---

## 22. CI/CD Validation

The metadata-only semantic layer does not require Redshift write access for CI/CD.

### Required pull-request checks

```bash
python -m pytest
python -m semantic_layer.validator definitions/
python -m semantic_layer.validator --check-references definitions/
python -m semantic_layer.validator --check-policies definitions/
python -m semantic_layer.validator --check-metric-catalog docs/metric-catalog.md
```

Where permitted, a controlled CI identity with read-only Redshift access may also run selected validation SQL:

```bash
python -m semantic_layer.run_validations \
  --connection redshift_readonly \
  --definitions definitions/ \
  --sql-dir sql/validation/
```

### Pipeline failure conditions

Fail validation if:

- YAML/JSON does not parse.
- Metric references a missing source, entity, dimension, or relationship.
- A metric is marked `approved` while required fields are unresolved.
- An approved metric lacks a grain, owner, time basis, or reconciliation reference.
- A source/table/column is not allowlisted.
- A prohibited PII/sensitive field is included in a standard semantic model.
- A query template permits raw user SQL or unparameterized values.
- A relationship lacks cardinality or validation requirements.
- Airline-specific ambiguities are not documented for an approved metric.

---

## 23. Implementation Plan

### Milestone 0: Repository and secure read-only access

**Deliverables**

- Semantic-layer Git repository.
- Secure Redshift read-only connection configuration stored in a secret manager or protected environment variables.
- No connection string, password, private key, or secret committed to Git.
- Initial repository folders, YAML schemas, and validation tooling.

**Acceptance criteria**

- A controlled read-only connection successfully executes discovery queries.
- The repository contains no hardcoded credentials.
- SQL policy permits only `SELECT` operations.

### Milestone 1: Source discovery

**Deliverables**

- `docs/source-data-inventory.md`.
- Accessible table/view list.
- Column-level inspection outputs.
- Candidate-key, timestamp, status, and cardinality profiles.
- Data classification draft.
- Airline business glossary draft with explicit unknowns.

**Acceptance criteria**

- At least one candidate source domain is documented.
- No grain, time basis, carrier role, or business interpretation is falsely assumed.
- Potential PII and sensitive fields are classified.

### Milestone 2: First draft semantic model

**Deliverables**

- One source-definition YAML file.
- One semantic-model YAML file with `status: draft`.
- One or more draft metric YAML definitions.
- Read-only validation SQL.
- Metric-catalog entries marked draft.

**Acceptance criteria**

- Source object and physical fields are allowlisted.
- Entity/field mappings match discovered source metadata.
- Required validation conditions are listed for each draft metric.

### Milestone 3: First approved semantic domain

**Deliverables**

- Confirmed fact grain and primary entity.
- Approved time semantics, organization scope, status mappings, and carrier-role treatment where applicable.
- Two to five approved, reconciled metrics.
- Reference SQL and reconciliation results.
- Query-policy enforcement tests.

**Acceptance criteria**

- Every approved metric has a business owner and technical owner.
- Metric outputs reconcile with approved direct SQL for selected periods.
- No prohibited field is exposed.
- All consumer queries can be constrained to allowlisted sources and dimensions.

### Milestone 4: Future service readiness

**Deliverables**

- Python semantic registry and query compiler.
- Parameterized `SELECT` query generation.
- Query limits, scope enforcement, audit logging, and error handling.
- Contract documentation for a future Streamlit application.

**Acceptance criteria**

- The service rejects unapproved metrics, unapproved dimensions, raw SQL, unallowlisted sources, and unsafe filters.
- The service can compile and execute a valid read-only metric request.
- The future Streamlit application can consume the semantic layer without direct raw-table logic.

---

## 24. Definition of Done

The read-only semantic-layer project is complete for a domain when:

- The source object is documented and accessible through read-only Redshift access.
- The source fact grain, primary key, duplicate behavior, time semantics, status meanings, and organizational scope are documented.
- Required airline-specific ambiguity decisions are recorded.
- Semantic models, entities, dimensions, relationships, measures, and metrics are version-controlled outside Redshift.
- Every approved metric has a clear business definition, owner, source, formula, time basis, allowed dimensions, data classification, and validation SQL.
- Metric results reconcile against direct, source-traceable read-only SQL.
- Sensitive passenger, crew, employee, payment, and security data are excluded or explicitly governed.
- CI/CD validates definitions, references, policies, and tests without requiring database write access.
- A future Streamlit app can request approved metrics through a structured semantic query contract rather than through unrestricted SQL.

---

## 25. Minimal First Release

Because the available airline data is not yet known, the first release should remain small and evidence-based.

```text
1. Create the semantic-layer repository.
2. Configure a secure, read-only Redshift connection.
3. Inventory accessible schemas, tables, views, and columns.
4. Select one source with the clearest candidate grain and keys.
5. Create one source definition in Draft status.
6. Create one semantic model in Draft status.
7. Define one primary entity, one organization/carrier scope entity if applicable,
   one time dimension, and two or three categorical dimensions.
8. Define no more than three draft metrics.
9. Add read-only validation and reconciliation SQL.
10. Promote a metric to Approved only after technical validation and business sign-off.
```

If discovery confirms a reliable flight-operations source, an appropriate first semantic domain may be:

```text
Semantic model: flight_operations
Primary entity: flight_operation
Candidate dimensions: operation_time, operation_status, origin_airport, destination_airport
Candidate metrics:
  - flight_operation_count
  - operated_flight_count
  - cancelled_flight_count
```

Do not add delay, on-time performance, passenger, load-factor, revenue, ticketing, codeshare, loyalty, maintenance, or financial metrics until their underlying data grain and business semantics have been explicitly validated.
