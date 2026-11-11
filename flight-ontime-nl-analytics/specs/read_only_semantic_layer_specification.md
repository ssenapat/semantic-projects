# Read-Only Transport Statistics Semantic Layer Specification for PostgreSQL (`tran-stats`)

## 1. Purpose

This specification defines a metadata-driven, read-only semantic layer for the existing PostgreSQL database `tran-stats`, which holds US domestic airline on-time performance data loaded from the BTS On-Time Reporting CSV (`T_ONTIME_REPORTING.csv`, January 2026, 544,003 flight rows).

The organization has read-only access to this database. The semantic layer must therefore **not create or modify any database objects** (tables, views, materialized views, schemas, roles, grants, functions, procedures). It is maintained outside the database as version-controlled metadata and, later, a restricted Python query compiler.

The semantic layer translates physical objects in schema `ontime` into approved business concepts: flights, carriers, airports, delays, cancellations, diversions, routes, and time periods. It is the single source of truth for business definitions; PostgreSQL remains the source of data and the query execution engine.

This replaces the earlier generic Redshift/discovery-first version of this document. Source discovery has been performed against the live database, and its results are recorded in Section 6.

## 2. Objectives

1. Create a governed business vocabulary over `ontime.flight`, `ontime.carrier` and `ontime.airport`.
2. Define reusable metrics, dimensions, relationships and policies in Git-managed YAML.
3. Support a default-deny approach for sensitive or unapproved data.
4. Prevent downstream applications (LLM intent parser, Streamlit app, API) from inventing metric formulas, joins or raw SQL.
5. Allow a future Python query compiler to generate approved, parameterized, `SELECT`-only SQL from structured semantic requests.
6. Reconcile every approved metric against the existing views and baseline counts in Section 14.

## 3. Scope

### In scope

- Semantic definitions for the `ontime` schema: `flight` (fact), `carrier`, `airport` (dimensions).
- Business glossary, ambiguity register and data-quality notes (Sections 6–7).
- Metric, dimension, relationship and policy definitions (Sections 9–12).
- Validation and reconciliation SQL (Section 14).
- The future query compiler contract (Section 13).
- Repository layout, versioning and CI (Sections 15–16).

### Out of scope

- Creating tables, views, roles or indexes (the existing `ontime.v_carrier_monthly_performance` and `ontime.v_route_summary` views are treated as read-only reconciliation references, not as dependencies).
- Changing the ETL in `sql/01_schema.sql`, `sql/02_transform.sql` or `scripts/load.sh`.
- Forecasting, ML, free-form text-to-SQL, or exposing raw SQL to end users.
- Loading additional months or other BTS datasets (the model must be extensible to them; see Section 7.3).

## 4. Constraint: Read-Only Access

### 4.1 Connection

The connection is configured through environment variables or a secret manager. **No credentials, connection strings or passwords may appear in this repository or in these specifications.**

| Setting | Value / variable |
|---|---|
| Engine | PostgreSQL (verified: 18.6, aarch64) |
| Host | `TRANSPORT_STATS_PG_HOST` (local dev: `localhost`) |
| Port | `TRANSPORT_STATS_PG_PORT` (local dev: `5432`) |
| Database | `tran-stats` — hyphenated, so it must be double-quoted wherever used as an SQL identifier |
| User / password | `TRANSPORT_STATS_PG_USER`, `TRANSPORT_STATS_PG_PASSWORD` |
| Default schema | `ontime` (all semantic objects live here; do not rely on `search_path`, always schema-qualify) |

> **Note.** The `postgres` database on the same server is empty; the data is in `tran-stats`. Local development currently uses the `postgres` superuser (see `README.md`). That is acceptable for local dev only. Any shared or non-local environment must use a dedicated least-privilege role (Section 4.3), provisioned by the database owner outside this project.

### 4.2 Allowed and prohibited operations

Allowed (read-only):

```sql
SELECT ... FROM ontime.flight ...;
SELECT ... FROM information_schema.columns ...;
SELECT ... FROM pg_catalog.pg_constraint ...;
```

Prohibited for the semantic layer and all consumers: `CREATE`, `ALTER`, `DROP`, `TRUNCATE`, `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `COPY ... FROM`, `GRANT`, `REVOKE`, `CALL`, `DO`, `SET ROLE`, `SELECT ... INTO`, `SELECT ... FOR UPDATE`, and any function with side effects (e.g. `pg_terminate_backend`, `set_config` for non-session settings, `lo_*`).

### 4.3 Enforced read-only session (defense in depth)

Convention alone is not sufficient. Every connection opened by the semantic layer or compiler must:

1. Use `default_transaction_read_only = on` or `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY` (psycopg: `connection.set_session(readonly=True)`).
2. Set `statement_timeout` (default 30 s) and `idle_in_transaction_session_timeout`.
3. Use a role that has only `CONNECT` on the database, `USAGE` on schema `ontime`, and `SELECT` on `ontime.flight`, `ontime.carrier`, `ontime.airport` (and the two reconciliation views). The role must not be a superuser or the table owner.
4. Execute only a single statement per request, and only statements that begin with `SELECT` or `WITH` after parsing (not by string prefix alone).

## 5. Target Architecture

```text
Git repository (semantic definitions: YAML + docs + validation SQL)
        |
        |  loads / validates / compiles
        v
Python semantic-layer service (future)  ---- read-only SELECT ---->  PostgreSQL "tran-stats", schema ontime
        ^
        |  structured semantic request (metric + dimensions + filters)
Future consumers: LLM intent parser, Streamlit app, API
```

### Architectural rule

The semantic layer owns business meaning. Consumers must not contain metric formulas, table or column names, join logic or raw SQL. They request approved metrics and dimensions by name; the compiler produces SQL. This is consistent with the three-service architecture (`three_service_natural_language_analytics_architecture_spec.md`), where the retriever exposes only LLM-safe fields (no `table`, `schema`, `column`, `sql`, `connection_string`, `password`).

## 6. Source Inventory (discovered 2026-10-12)

### 6.1 Objects in schema `ontime`

| Object | Type | Rows | Role |
|---|---|---|---|
| `ontime.flight` | base table | 544,003 | Fact: one row per scheduled flight leg |
| `ontime.airport` | base table | 342 | Dimension: airports (origin and destination) |
| `ontime.carrier` | base table | 13 | Dimension: operating carriers |
| `ontime.v_carrier_monthly_performance` | view | — | Reconciliation reference only |
| `ontime.v_route_summary` | view | 5,812 | Reconciliation reference only |

The `public` schema contains no objects. The staging table `stg_flight` is dropped after load and must never be referenced.

### 6.2 `ontime.flight` (fact)

Grain: **one row per flight leg** (`flight_id` is a surrogate identity key assigned at load time, 1…544,003; it is *not* a BTS flight number and has no business meaning). There is no tail number, flight number, scheduled/actual time, delay-minutes or distance-in-miles column in this database. Delays are available only as a 15-minute flag and as BTS delay groups.

| Column | Type | Null | Semantics |
|---|---|---|---|
| `flight_id` | bigint identity PK | no | Surrogate key |
| `fl_date` | date | no | Flight (departure) date, 2026-01-01 … 2026-01-31 (31 distinct days) |
| `quarter` | smallint 1–4 | no | Calendar quarter; currently always 1 |
| `month` | smallint 1–12 | no | Calendar month; currently always 1 |
| `day_of_week` | smallint 1–7 | no | BTS convention: 1 = Monday … 7 = Sunday |
| `airline_id` | integer FK → `carrier.airline_id` | no | Operating carrier id |
| `op_unique_carrier` | varchar(2) | no | Operating carrier code; always equals `carrier.unique_carrier` (verified, 0 mismatches) |
| `op_carrier` | varchar(2) | no | Operating carrier code; identical to `op_unique_carrier` in current data (redundant) |
| `origin_airport_id` | integer FK → `airport.airport_id` | no | Origin airport |
| `dest_airport_id` | integer FK → `airport.airport_id` | no | Destination airport |
| `dep_del15` | smallint (0/1) | **yes** | 1 = departed ≥15 min late. NULL for 25,229 flights (cancelled with no departure record) |
| `dep_delay_group` | smallint | **yes** | BTS departure-delay interval group, range −2…12 |
| `dep_time_blk` | char(9) | no | Scheduled departure hour block, e.g. `0600-0659` (19 values) |
| `arr_del15` | smallint (0/1) | **yes** | 1 = arrived ≥15 min late. NULL for 26,781 flights (all cancelled or diverted) |
| `arr_delay_group` | smallint | **yes** | BTS arrival-delay interval group, range −2…12 |
| `arr_time_blk` | char(9) | no | Scheduled arrival hour block (19 values) |
| `cancelled` | smallint (0/1) | no | 1 = cancelled (25,635 flights) |
| `cancellation_code` | char(1) | **yes** | A, B, C, D; non-NULL exactly when `cancelled = 1` |
| `diverted` | smallint (0/1) | no | 1 = diverted (1,146 flights) |
| `distance_group` | smallint | no | BTS distance interval group, 1…11 |
| `div_airport_landings` | smallint | no* | Number of landings at diversion airports (populated for diverted flights; see 7.2) |
| `div_reached_dest` | smallint | **yes** | 1 = diverted flight eventually reached destination |
| `div1_airport_id` … `div5_airport_id` | integer | yes | Diversion airports (no FK is enforced) |
| `div1_airport_seq_id` … `div5_airport_seq_id` | integer | yes | Diversion airport sequence ids |

Indexes: PK on `flight_id`; `ix_flight_fl_date (fl_date)`; `ix_flight_origin (origin_airport_id)`; `ix_flight_dest (dest_airport_id)`; `ix_flight_carrier (airline_id, fl_date)`.

\* `div_airport_landings` contains no NULLs. It is 0 for 542,737 ordinary flights, 1 or 2 for diverted flights, and 9 for 120 non-diverted flights (see 7.2 #3).

### 6.3 `ontime.airport` (dimension)

| Column | Type | Null | Semantics |
|---|---|---|---|
| `airport_id` | integer PK | no | BTS airport id (join key) |
| `airport_seq_id` | integer | no | BTS airport sequence id (unique per airport in this data) |
| `city_market_id` | integer | no | BTS city-market id (319 distinct; several airports can share a market) |
| `code` | char(3) | **yes** | IATA-style code. NULL for the 1 airport that appears only as a destination |
| `city_name` | varchar(60) | no | e.g. `Chicago, IL` |
| `state_abr` | char(2) | no | State/territory abbreviation (52 distinct) |
| `wac` | smallint | **yes** | BTS World Area Code. NULL for destination-only airports |

341 airports appear as an origin; 342 as a destination. Code and WAC come only from origin-side rows in the source CSV, so destination-only airports have NULL `code`/`wac`.

### 6.4 `ontime.carrier` (dimension)

| Column | Type | Semantics |
|---|---|---|
| `airline_id` | integer PK | BTS carrier id |
| `unique_carrier` | varchar(2) NOT NULL | Carrier code (13 distinct values; no unique constraint is declared, so uniqueness is a validation check) |

The 13 carriers and their January 2026 flight counts: `AA` 76,772 · `AS` 26,436 · `B6` 18,109 · `DL` 79,198 · `F9` 15,382 · `G4` 8,963 · `MQ` 24,155 · `NK` 11,915 · `OH` 19,550 · `OO` 66,168 · `UA` 63,781 · `WN` 104,002 · `YX` 29,572. **There is no carrier-name column.** Display names (e.g. "Southwest Airlines" for `WN`) are *not* in the database and must come from an approved reference mapping in the semantic layer (`code_mappings`, Section 9.4) — never from model inference.

### 6.5 Declared constraints and relationships

- `flight.airline_id` → `carrier.airline_id` (FK `flight_airline_id_fkey`)
- `flight.origin_airport_id` → `airport.airport_id` (FK `flight_origin_airport_id_fkey`)
- `flight.dest_airport_id` → `airport.airport_id` (FK `flight_dest_airport_id_fkey`)
- CHECKs: `quarter` 1–4, `month` 1–12, `day_of_week` 1–7, `cancelled`/`diverted`/`dep_del15`/`arr_del15` ∈ {0,1}.
- No comments (`COMMENT ON`) exist on any object; all documentation lives in this repository.

### 6.6 Re-discovery

Because the data will change when more months are loaded, discovery must be repeatable. Use read-only queries only:

```sql
-- Objects
SELECT table_schema, table_name, table_type
FROM information_schema.tables
WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
ORDER BY 1, 2;

-- Columns
SELECT table_name, ordinal_position, column_name, data_type,
       character_maximum_length, is_nullable
FROM information_schema.columns
WHERE table_schema = 'ontime'
ORDER BY table_name, ordinal_position;

-- Constraints (keys, FKs, checks)
SELECT conrelid::regclass AS table_name, conname, contype, pg_get_constraintdef(oid) AS definition
FROM pg_catalog.pg_constraint
WHERE connamespace = 'ontime'::regnamespace
ORDER BY 1, 2;

-- Row counts (approximate, no table scan) and exact date coverage
SELECT c.relname, c.reltuples::bigint AS approx_rows
FROM pg_catalog.pg_class c
WHERE c.relnamespace = 'ontime'::regnamespace AND c.relkind = 'r';

SELECT MIN(fl_date) AS first_date, MAX(fl_date) AS last_date, COUNT(*) AS rows
FROM ontime.flight;
```

Profile-style queries must be date-bounded or aggregated; never `SELECT *` without `LIMIT`.

## 7. Domain Interpretation Rules

### 7.1 Business definitions

| Term | Definition in this layer |
|---|---|
| Flight | One row in `ontime.flight` (a scheduled flight leg), regardless of outcome |
| Operated flight | A flight with `cancelled = 0` (includes diverted flights) |
| Cancelled flight | `cancelled = 1`; reason in `cancellation_code` |
| Diverted flight | `diverted = 1` |
| Departure delayed | `dep_del15 = 1` (≥15 minutes late). Not a delay in minutes |
| Arrival delayed | `arr_del15 = 1` (≥15 minutes late) |
| On-time arrival | `arr_del15 = 0` — i.e. arrived less than 15 minutes late. This is the BTS "on-time" convention |
| Route | Ordered pair (origin airport, destination airport); `JFK→LAX` ≠ `LAX→JFK` |
| Carrier | Operating carrier (`op_unique_carrier`). Marketing carrier is **not** available |

### 7.2 Data-quality facts that affect metric definitions

1. **Delay flags are NULL, not 0, when the flight did not complete normally.** `arr_del15` is NULL for all 25,635 cancelled flights and all 1,146 diverted flights (26,781 total); `dep_del15` is NULL for 25,229 of the cancelled flights.
   Verified combinations of (`cancelled`, `diverted`, `arr_del15` null?, `dep_del15` null?):

   | cancelled | diverted | arr_del15 NULL | dep_del15 NULL | rows |
   |---|---|---|---|---|
   | 0 | 0 | no | no | 517,222 |
   | 0 | 1 | yes | no | 1,146 |
   | 1 | 0 | yes | no | 406 |
   | 1 | 0 | yes | yes | 25,229 |

   Consequence: delay-rate metrics must define their denominator explicitly as flights with a non-NULL flag (this matches `AVG(dep_del15)` / `AVG(arr_del15)`, which ignore NULLs, and therefore matches the existing views). Delay metrics must **not** use `COUNT(*)` as denominator.
2. **Cancelled flights can still have a departure flag.** 406 cancelled flights have `dep_del15` populated (cancelled after pushback). Departure metrics therefore need an explicit decision — this layer uses the non-NULL-flag denominator and documents it; business owners may choose to restrict to `cancelled = 0` (open question Q3).
3. **`div_airport_landings` > 0 on non-diverted flights.** 120 flights with `diverted = 0` have `div_airport_landings = 9` (an anomalous value; diverted flights have 1 or 2). Diversion metrics must use the `diverted` flag, never `div_airport_landings`.
4. **`flight_id` is not a flight number.** `COUNT(DISTINCT flight_id)` equals `COUNT(*)` and must not be presented as "distinct flights/aircraft".
5. **Month/quarter are constants** in the loaded data (always 1). Time-trend metrics are meaningful only at day or day-of-week level until more months are loaded.
6. **Delay and distance groups are coded intervals**, not minutes or miles. Do not compute average delay minutes, total miles or load factor — the data does not exist.
7. **Destination-only airport:** exactly one airport (`code IS NULL`) is never an origin. Any airport-code filter or label on the destination side must handle NULL (fall back to `city_name`).
8. **No time zone, scheduled/actual clock times, tail number, passengers, seats, revenue, fares or cargo** exist in this database. Questions about them must return `unsupported` (Section 12.2).

### 7.3 Ambiguities requiring business confirmation

| # | Question | Default until answered |
|---|---|---|
| Q1 | Is "delayed" departure delay, arrival delay, or either? | Ask for clarification; never default silently |
| Q2 | Should "on-time rate" count cancelled/diverted flights as not on time? | No — BTS convention; denominator = flights with non-NULL `arr_del15` |
| Q3 | Should departure delay rate exclude cancelled flights that still have `dep_del15`? | Include (non-NULL denominator); flagged in metric notes |
| Q4 | Does "airport" in a question mean origin or destination? | Require explicit role; "flights at X" is ambiguous |
| Q5 | Carrier display names (code → name) | Not stored in DB; approved mapping must be supplied |
| Q6 | Meaning of delay-group and distance-group intervals | BTS documented intervals (below); confirm before exposing as labels |
| Q7 | Reporting-period convention once more months are loaded | By `fl_date`, calendar month |

Reference mappings to be confirmed (BTS convention):

- `cancellation_code`: A = Carrier, B = Weather, C = National Air System, D = Security.
- `distance_group`: 11 groups in 250-mile increments (1 = under 250 mi … 11 = 2,500 mi or more).
- `dep_delay_group` / `arr_delay_group`: −2 = more than 15 min early, −1 = 0–14 min early, 0 = 0–14 min late, 1 = 15–29 min late, … 12 = 180+ min late.
- `day_of_week`: 1 = Monday … 7 = Sunday.

## 8. Semantic Modeling Standard

### 8.1 Semantic concepts

| Concept | Meaning |
|---|---|
| Source | A governed physical table or view that may be queried (`ontime.flight`, `ontime.carrier`, `ontime.airport`) |
| Semantic model | A governed grouping of one fact source with its dimensions |
| Entity | A business object (flight, carrier, airport, route) |
| Dimension | An attribute used to filter, group or label |
| Measure | A base aggregation over a column or expression |
| Metric | A business-approved calculation built from measures, with filters, denominator and null handling |
| Relationship | A governed join with cardinality |
| Policy | Rules on permitted sources, columns, filters, limits |

### 8.2 Definition states

`draft` → `validated` (technically reconciled against the database) → `approved` (business owner sign-off) → `deprecated`. Only `approved` objects may be exposed to consumers. No object may be promoted from `draft` to `approved` without a validation SQL result and a recorded business owner.

### 8.3 Naming standards

- Semantic names: lowercase `snake_case`, singular domain nouns (`flight`, `carrier`, `airport`).
- Metrics: business name with explicit basis, e.g. `arrival_delay_rate`, not `delay_pct`.
- Physical identifiers in YAML are always schema-qualified (`ontime.flight`); the database name `"tran-stats"` is quoted wherever it is used as an identifier.
- Versioned metrics append a suffix on breaking change (`arrival_delay_rate_v2`).

### 8.4 Mandatory attributes for every approved metric

`name`, `label`, `description`, `status`, `version`, `business_owner`, `technical_owner`, `source`, `grain`, `expression` (or formula), `numerator`/`denominator` for any ratio, `null_handling`, `filters`, `allowed_dimensions`, `unit` (`count` | `percent` | `ratio`), `time_basis` (`fl_date`), `validation_sql`, `reconciliation` (reference value and tolerance), and `change_log`.

## 9. Semantic Model Definitions

### 9.1 Source definitions

```yaml
# definitions/sources/postgres_sources.yml
version: 1
connection:
  engine: postgresql
  database: "tran-stats"            # hyphenated: always quote in SQL
  credentials: env                  # TRANSPORT_STATS_PG_* variables; never stored here
  read_only: true
sources:
  - name: flight_facts
    physical_object: ontime.flight
    object_type: table
    grain: one row per flight leg (flight_id)
    primary_key: [flight_id]
    classification: internal
    status: validated
    business_owner: <team_or_role>
    technical_owner: <data_platform_owner>
    freshness: static load; currently 2026-01-01..2026-01-31
    time_column: fl_date
  - name: carriers
    physical_object: ontime.carrier
    object_type: table
    grain: one row per carrier
    primary_key: [airline_id]
    classification: internal
    status: validated
  - name: airports
    physical_object: ontime.airport
    object_type: table
    grain: one row per airport
    primary_key: [airport_id]
    classification: internal
    status: validated
  # Reconciliation-only; never queryable by consumers
  - name: ref_carrier_monthly_performance
    physical_object: ontime.v_carrier_monthly_performance
    object_type: view
    usage: reconciliation_only
  - name: ref_route_summary
    physical_object: ontime.v_route_summary
    object_type: view
    usage: reconciliation_only
```

### 9.2 Semantic model: `flight_operations`

```yaml
# definitions/semantic_models/flight_operations.yml
version: 1
semantic_models:
  - name: flight_operations
    label: Flight Operations
    status: validated
    primary_source: flight_facts
    grain: one row per flight leg
    time_dimension: flight_date

    entities:
      - { name: flight,  type: primary, column: flight_id }
      - { name: carrier, type: foreign, column: airline_id,        references: carriers.airline_id }
      - { name: origin_airport, type: foreign, column: origin_airport_id, references: airports.airport_id }
      - { name: destination_airport, type: foreign, column: dest_airport_id, references: airports.airport_id }

    dimensions:
      # --- time ---
      - { name: flight_date,   label: Flight Date,   type: time, column: fl_date, granularities: [day, week, month] }
      - { name: day_of_week,   label: Day of Week,   type: categorical, column: day_of_week,
          values: { 1: Monday, 2: Tuesday, 3: Wednesday, 4: Thursday, 5: Friday, 6: Saturday, 7: Sunday } }
      - { name: month,         label: Month,         type: categorical, column: month,
          note: constant (1) in current data }
      - { name: quarter,       label: Quarter,       type: categorical, column: quarter,
          note: constant (1) in current data }
      - { name: departure_time_block, label: Scheduled Departure Hour, type: categorical, column: dep_time_blk }
      - { name: arrival_time_block,   label: Scheduled Arrival Hour,   type: categorical, column: arr_time_blk }

      # --- carrier (via carriers) ---
      - { name: carrier_code, label: Carrier Code, type: categorical, source: carriers, column: unique_carrier,
          mapping: carrier_display_names }

      # --- airport (role-playing: origin / destination) ---
      - { name: origin_airport_code,  label: Origin Airport,      type: categorical, source: airports, role: origin_airport,      column: code }
      - { name: origin_city,          label: Origin City,         type: categorical, source: airports, role: origin_airport,      column: city_name }
      - { name: origin_state,         label: Origin State,        type: categorical, source: airports, role: origin_airport,      column: state_abr }
      - { name: destination_airport_code, label: Destination Airport, type: categorical, source: airports, role: destination_airport, column: code,
          null_policy: label_with_city_name }
      - { name: destination_city,     label: Destination City,    type: categorical, source: airports, role: destination_airport, column: city_name }
      - { name: destination_state,    label: Destination State,   type: categorical, source: airports, role: destination_airport, column: state_abr }

      # --- operational ---
      - { name: cancellation_reason,  label: Cancellation Reason, type: categorical, column: cancellation_code,
          values: { A: Carrier, B: Weather, C: National Air System, D: Security },
          null_policy: not_cancelled, status: draft }
      - { name: distance_group,       label: Distance Group,      type: ordinal, column: distance_group, values: [1,2,3,4,5,6,7,8,9,10,11], status: draft }
      - { name: departure_delay_group, label: Departure Delay Group, type: ordinal, column: dep_delay_group, status: draft }
      - { name: arrival_delay_group,   label: Arrival Delay Group,   type: ordinal, column: arr_delay_group,  status: draft }

    measures:
      - name: flight_count_base
        aggregation: count
        expression: "*"
      - name: cancelled_count_base
        aggregation: sum
        expression: cancelled
      - name: diverted_count_base
        aggregation: sum
        expression: diverted
      - name: dep_delayed_count_base
        aggregation: sum
        expression: dep_del15
      - name: arr_delayed_count_base
        aggregation: sum
        expression: arr_del15
      - name: dep_flagged_count_base
        aggregation: count
        expression: dep_del15          # COUNT(col) ignores NULL
      - name: arr_flagged_count_base
        aggregation: count
        expression: arr_del15

    # Disallowed physical columns (not exposed as dimensions)
    excluded_columns:
      - { column: flight_id,           reason: surrogate key, no business meaning }
      - { column: op_unique_carrier,   reason: duplicate of carriers.unique_carrier }
      - { column: op_carrier,          reason: duplicate of op_unique_carrier }
      - { column: div_airport_landings, reason: inconsistent with diverted flag (7.2 #3), pending owner decision }
      - { column: "div1..div5 airport id / seq id", reason: diversion detail out of first release }
```

### 9.3 Mixed-role airport joins

`ontime.airport` is joined twice from `flight` (origin and destination). The compiler must alias each role separately (e.g. `ao` for origin, `ad` for destination) and must reject a request that references an airport dimension without a role.

### 9.4 Code mappings

```yaml
# definitions/semantic_models/code_mappings.yml
mappings:
  - name: carrier_display_names
    status: draft                       # not present in the database; requires approved source
    keys_from: ontime.carrier.unique_carrier
    values: {}                          # to be filled from an approved reference (do not guess)
    unmapped_policy: show_code
  - name: cancellation_reasons
    status: draft
    values: { A: Carrier, B: Weather, C: National Air System, D: Security }
```

## 10. Metric Definitions

All metrics use `time_basis: fl_date`, `source: flight_facts`, grain `flight`. Percent values are returned as a ratio 0–1 by the compiler and formatted by the presentation layer, **except** where the reconciliation views already round to a percentage (Section 14).

```yaml
# definitions/metrics/flight_operations_metrics.yml
version: 1
metrics:
  - name: flight_count
    label: Flights
    description: Number of scheduled flight legs, including cancelled and diverted flights.
    status: validated
    unit: count
    expression: COUNT(*)
    null_handling: none needed
    allowed_dimensions: [flight_date, day_of_week, departure_time_block, arrival_time_block, carrier_code,
                         origin_airport_code, origin_city, origin_state,
                         destination_airport_code, destination_city, destination_state,
                         distance_group, cancellation_reason]
    combinable_with: flight_operations   # group tag, see 10.3
    baseline: { scope: all, value: 544003 }

  - name: operated_flight_count
    label: Operated Flights
    description: Flights that were not cancelled (includes diverted flights).
    status: validated
    unit: count
    expression: COUNT(*)
    filters: ["cancelled = 0"]
    baseline: { scope: all, value: 518368 }

  - name: cancelled_flight_count
    label: Cancelled Flights
    status: validated
    unit: count
    expression: SUM(cancelled)
    baseline: { scope: all, value: 25635 }

  - name: cancellation_rate
    label: Cancellation Rate
    description: Cancelled flights divided by all scheduled flights.
    status: validated
    unit: ratio
    numerator: SUM(cancelled)
    denominator: COUNT(*)
    baseline: { scope: all, value: 0.0471 }      # 25,635 / 544,003

  - name: diverted_flight_count
    label: Diverted Flights
    status: validated
    unit: count
    expression: SUM(diverted)
    baseline: { scope: all, value: 1146 }

  - name: diversion_rate
    label: Diversion Rate
    description: Diverted flights divided by operated (non-cancelled) flights.
    status: draft                                 # denominator choice needs owner confirmation
    unit: ratio
    numerator: SUM(diverted)
    denominator: COUNT(*) FILTER (WHERE cancelled = 0)

  - name: departure_delay_rate
    label: Departure Delay Rate (15+ min)
    description: Share of flights with a departure delay flag that departed 15 or more minutes late.
    status: validated
    unit: ratio
    numerator: SUM(dep_del15)
    denominator: COUNT(dep_del15)                 # excludes NULL flags
    null_handling: NULL dep_del15 excluded from numerator and denominator
    baseline: { scope: all, value: 0.2068 }       # matches ROUND(100*AVG(dep_del15),2)
    open_question: Q3

  - name: arrival_delay_rate
    label: Arrival Delay Rate (15+ min)
    description: Share of flights with an arrival flag that arrived 15 or more minutes late.
    status: validated
    unit: ratio
    numerator: SUM(arr_del15)
    denominator: COUNT(arr_del15)                 # excludes cancelled and diverted
    baseline: { scope: all, value: 0.2078 }

  - name: on_time_arrival_rate
    label: On-Time Arrival Rate
    description: Share of flights with an arrival flag that arrived less than 15 minutes late (BTS convention).
    status: draft                                 # Q2 pending business approval
    unit: ratio
    numerator: COUNT(*) FILTER (WHERE arr_del15 = 0)
    denominator: COUNT(arr_del15)
    baseline: { scope: all, value: 0.7922 }       # 1 - arrival_delay_rate

  - name: departure_delayed_flight_count
    label: Flights Departing 15+ Min Late
    status: validated
    unit: count
    expression: SUM(dep_del15)
    baseline: { scope: all, value: 107297 }       # 2026-10-12; denominator COUNT(dep_del15) = 518774

  - name: arrival_delayed_flight_count
    label: Flights Arriving 15+ Min Late
    status: validated
    unit: count
    expression: SUM(arr_del15)
    baseline: { scope: all, value: 107475 }       # 2026-10-12; denominator COUNT(arr_del15) = 517222
```

> Baselines were taken from live data on 2026-10-12 (departure rate 20.68 %, arrival rate 20.78 %, cancellation rate 4.71 %). They are reference values for reconciliation and must be re-baselined whenever data is reloaded (Section 17, Milestone 5).

### 10.1 Metric catalog standard

Each approved metric also has a Markdown entry in `docs/metric-catalog.md` using this template:

```markdown
## <metric_name>
- Label / Status / Version
- Business owner / Technical owner
- Business definition (one sentence, plain language)
- Source, grain, time basis
- Formula (numerator, denominator, filters)
- Null handling and exclusions
- Allowed dimensions and `combinable_with` group
- Known caveats (reference Section 7.2 items)
- Validation SQL and reconciliation result
- Change log
```

### 10.2 Metrics that must be refused

These cannot be computed from this database and must return `unsupported`: average delay minutes, total delay minutes, passenger counts, load factor, revenue, fares, seat capacity, flight distance in miles, aircraft/tail utilisation, scheduled vs actual clock times, marketing-carrier or codeshare metrics, and any carrier display-name filter without an approved mapping.

### 10.3 Combining metrics (`combinable_with`)

- Each metric carries an optional `combinable_with` group tag (a string, for example `flight_operations`). Metrics can be requested together in one query only when they share the same non-null tag. The loader enforces that every metric in a group shares the same semantic model, grain and `time_basis`. The tag is not part of `definition_hash`, so adding it does not bump a metric version.
- All `flight_operations` metrics currently share the group `flight_operations`.
- The tag is part of the LLM-safe metric (`LlmSafeMetric.combinable_with`), so the parser can check combinability without seeing model, grain or time basis.
- A ratio metric is computed from its own numerator and denominator per group, never from other columns in the query, so counts and ratios can be mixed (D4).

## 11. Relationship Definitions

```yaml
# definitions/relationships/transport_relationships.yml
relationships:
  - name: flight_to_carrier
    left: flight_facts.airline_id
    right: carriers.airline_id
    type: many_to_one
    join: inner
    enforced_fk: true
    status: validated
  - name: flight_to_origin_airport
    left: flight_facts.origin_airport_id
    right: airports.airport_id
    type: many_to_one
    join: inner
    role: origin_airport
    enforced_fk: true
    status: validated
  - name: flight_to_destination_airport
    left: flight_facts.dest_airport_id
    right: airports.airport_id
    type: many_to_one
    join: inner
    role: destination_airport
    enforced_fk: true
    status: validated
```

Relationship rules:

- All three joins are many-to-one against unique primary keys and are FK-enforced, so inner joins cannot drop or duplicate flight rows (verified: row counts are unchanged by the joins).
- Fact-to-fact joins are prohibited (there is only one fact table).
- A join not listed here must never be generated. Joining `airport` on `code` or `city_market_id` is prohibited (not unique / may be NULL).
- Route-level analysis is expressed as the pair (`origin_airport_code`, `destination_airport_code`); no `route` table exists, and the reconciliation view `ontime.v_route_summary` must not be queried by consumers.

## 12. Data Policy Specification

### 12.1 Default-deny

Any table, column or dimension that is not listed in an approved semantic model is not queryable. New columns added to the database are denied until explicitly modeled.

This dataset is public BTS aggregate-level flight data. It contains **no personally identifiable information** (no passenger, crew, payment or booking data), and classification is `internal` for all sources. The deny-by-default list remains in force for future sources:

```yaml
# definitions/policies/data_policy.yml
default: deny
classification_defaults:
  ontime.flight:  internal
  ontime.carrier: internal
  ontime.airport: internal
prohibited_column_patterns: [passenger, crew, name_first, name_last, email, phone, ssn, passport, payment, card, password, secret, token]
```

### 12.2 Query policy

```yaml
# definitions/policies/query_policy.yml
database_access: read_only
allowed_statement_types: [SELECT]
prohibited_statement_types: [INSERT, UPDATE, DELETE, MERGE, CREATE, ALTER, DROP, TRUNCATE, GRANT, REVOKE, COPY, CALL, DO]
allowed_sources: [ontime.flight, ontime.carrier, ontime.airport]
reconciliation_only_sources: [ontime.v_carrier_monthly_performance, ontime.v_route_summary]
prohibit_raw_sql_from_consumers: true
prohibit_llm_generated_sql: true
require_date_filter: false             # data is a single month; set to true when more months are loaded
max_result_rows: 1000
default_limit: 100
max_metrics_per_query: 5               # single source of truth; read by the compiler and by every service
statement_timeout_seconds: 30
require_metric_allowlist: true
unsupported_when:
  - metric_not_in_catalog
  - dimension_not_allowed_for_metric
  - refers_to_unavailable_data       # passengers, revenue, delay minutes, etc. (10.2)
clarify_when:
  - delay_direction_unspecified      # Q1
  - airport_role_unspecified         # Q4
ambiguous_terms:                       # optional per-term `question`
  - reason_code: airport_role_unspecified
    question: "Do you mean the origin airport, the destination airport, or both?"   # D1
glossary_terms:                        # D2; every maps_to entry must be a catalog metric or dimension name
  - term: carrier
    meaning: "Operating carrier (carrier_code). Marketing carrier is not supported."
    maps_to: [carrier_code]
```

- `max_metrics_per_query` (an integer of at least 1) is required. If it is missing or invalid the loader raises `DefinitionError`. The compiler reads it through the loader. The parser, query service and retrieval service each read the same file through `app/policy_limits.py`; no other copy of the number exists.
- `ambiguous_terms[*].question` is optional. For `airport_role_unspecified` it is the clarification question for an unqualified "airport" (D1): origin, destination or both.
- `glossary_terms` is an optional list of `{term, meaning, maps_to}` entries (all three required). The retrieval service adds them to the LLM-safe glossary. The D2 entry maps "carrier" to `carrier_code`; marketing carrier stays unsupported.

### 12.3 Scope policy

There is a single organization-wide dataset; no tenant or organization scoping is required today. If multi-tenant data is introduced, scope must come from trusted server-side identity context, never from browser- or user-supplied values.

## 13. Future Query Compiler Specification

The compiler is the planned bridge between the semantic layer and consumers. It generates **PostgreSQL** SQL only from approved definitions.

### 13.1 Input contract

```json
{
  "metric": "arrival_delay_rate",
  "dimensions": ["carrier_code"],
  "filters": [
    {"dimension": "flight_date", "operator": "between", "value": ["2026-01-01", "2026-01-15"]}
  ],
  "order_by": [{"field": "arrival_delay_rate", "direction": "desc"}],
  "limit": 20
}
```

The request takes exactly one of `metric` (a string; the compiler keeps this legacy input for the CLI, reconciliation and tests) or `metrics` (a list of 1 to `max_metrics_per_query` unique names). The query service always sends `metrics`. `metrics_not_combinable` is returned as `unsupported` when the metrics differ in model, grain, `time_basis` or `combinable_with`, or when a `combinable_with` tag is missing with more than one metric.

### 13.2 Required behavior

1. Load approved definitions only (`status: approved`; `validated` allowed only in non-production).
2. Resolve the metric and dimensions; reject any not in the catalog or not in the metric's `allowed_dimensions`.
3. Resolve physical expressions and the joins required by the relationships in Section 11, aliasing each airport role separately.
4. Apply policies: statement type, row limit, timeout, filter allowlist.
5. **Bind all values as parameters** (psycopg `%(name)s`); never interpolate user values into SQL. Identifiers come only from definitions, are schema-qualified and quoted.
6. Execute in a read-only session (Section 4.3) and log request id, metric, dimensions, filters, row count and duration. Never log credentials.
7. **Several metrics.** Emit one `SELECT` with each measure aliased by its metric name (in request order) and one `GROUP BY` item per dimension. All metrics share one source and grain, so there is no fan-out.
8. **Ordering.** Explicit `order_by` items first, then the first metric descending (NULLS LAST) unless already listed, then each dimension ascending. `ORDER BY` is always present, so results are deterministic.
9. **Limit and truncation.** `effective_limit = min(limit, max_result_rows)`. The SQL uses `LIMIT effective_limit + 1`. `execute` sets `truncated = len(rows) > effective_limit` and drops the extra row, so `row_count` is the returned count and the audit record logs the trimmed count.
10. Return an `unsupported` or clarification response — not a guessed query — for ambiguous or unavailable requests.

### 13.3 Example compiled SQL shape

```sql
SELECT c.unique_carrier                                   AS carrier_code,
       SUM(f.arr_del15)::numeric / NULLIF(COUNT(f.arr_del15), 0) AS arrival_delay_rate
FROM ontime.flight  AS f
JOIN ontime.carrier AS c ON c.airline_id = f.airline_id
WHERE f.fl_date BETWEEN %(start_date)s AND %(end_date)s
GROUP BY c.unique_carrier
ORDER BY arrival_delay_rate DESC
LIMIT %(limit)s;   -- limit = effective_limit + 1, see 13.2
```

Multi-metric, multi-dimension shape (`metrics=[flight_count, cancellation_rate]`, `dimensions=[carrier_code]`):

```sql
SELECT c.unique_carrier AS carrier_code,
       COUNT(*) AS flight_count,
       SUM(f.cancelled)::numeric / NULLIF(COUNT(*), 0) AS cancellation_rate
FROM ontime.flight AS f
JOIN ontime.carrier AS c ON c.airline_id = f.airline_id
GROUP BY c.unique_carrier
ORDER BY flight_count DESC NULLS LAST, carrier_code ASC
LIMIT %(limit)s;   -- limit = effective_limit + 1
```

Time-grain example (month grain uses `DATE_TRUNC`, as the existing views do):

```sql
SELECT DATE_TRUNC('month', f.fl_date)::date AS flight_month,
       COUNT(*) AS flight_count
FROM ontime.flight AS f
WHERE f.fl_date >= %(start_date)s AND f.fl_date < %(end_date)s
GROUP BY 1
ORDER BY 1;
```

Two-airport-role example:

```sql
SELECT ao.code AS origin_airport_code,
       ad.code AS destination_airport_code,
       COUNT(*) AS flight_count
FROM ontime.flight  AS f
JOIN ontime.airport AS ao ON ao.airport_id = f.origin_airport_id
JOIN ontime.airport AS ad ON ad.airport_id = f.dest_airport_id
GROUP BY ao.code, ad.code
ORDER BY flight_count DESC
LIMIT %(limit)s;
```

Always divide with `NULLIF(denominator, 0)` and cast to `numeric` to avoid integer division.

## 14. Validation and Reconciliation

### 14.1 Source validation (run in CI against a read-only connection)

```sql
-- Key uniqueness and null checks
SELECT COUNT(*) AS total_rows,
       COUNT(DISTINCT flight_id) AS distinct_keys,
       COUNT(*) FILTER (WHERE flight_id IS NULL) AS null_keys
FROM ontime.flight;

-- Referential integrity (expected 0, 0)
SELECT COUNT(*) FILTER (WHERE c.airline_id IS NULL)  AS orphan_carriers,
       COUNT(*) FILTER (WHERE ao.airport_id IS NULL OR ad.airport_id IS NULL) AS orphan_airports
FROM ontime.flight f
LEFT JOIN ontime.carrier c  ON c.airline_id = f.airline_id
LEFT JOIN ontime.airport ao ON ao.airport_id = f.origin_airport_id
LEFT JOIN ontime.airport ad ON ad.airport_id = f.dest_airport_id;

-- Date coverage and flag integrity
SELECT MIN(fl_date), MAX(fl_date), COUNT(DISTINCT fl_date) FROM ontime.flight;
SELECT cancelled, diverted, (arr_del15 IS NULL) AS arr_null, (dep_del15 IS NULL) AS dep_null, COUNT(*)
FROM ontime.flight GROUP BY 1,2,3,4 ORDER BY 1,2,3,4;

-- Cancellation code consistency (expected 0)
SELECT COUNT(*) FROM ontime.flight WHERE (cancelled = 1) <> (cancellation_code IS NOT NULL);
```

### 14.2 Metric reconciliation

Every metric must reconcile with an independent reference query, and with the existing views where one applies.

```sql
-- Baselines (expected 2026-10-12): 544003 / 25635 / 1146 / 0.2068 / 0.2078
SELECT COUNT(*)                                  AS flights,
       SUM(cancelled)                            AS cancelled,
       SUM(diverted)                             AS diverted,
       ROUND(AVG(dep_del15)::numeric, 4)         AS departure_delay_rate,
       ROUND(AVG(arr_del15)::numeric, 4)         AS arrival_delay_rate
FROM ontime.flight;

-- Carrier-month: semantic result vs existing view (differences must be 0)
SELECT s.unique_carrier, s.month, s.flights,
       s.flights - v.flights                 AS flights_diff,
       s.pct_arr - v.pct_arr_delayed_15      AS arr_pct_diff
FROM (
  SELECT c.unique_carrier, DATE_TRUNC('month', f.fl_date)::date AS month, COUNT(*) AS flights,
         ROUND(100.0 * AVG(f.arr_del15), 2) AS pct_arr
  FROM ontime.flight f JOIN ontime.carrier c USING (airline_id) GROUP BY 1, 2
) s
JOIN ontime.v_carrier_monthly_performance v USING (unique_carrier, month)
WHERE s.flights <> v.flights OR s.pct_arr <> v.pct_arr_delayed_15;
-- expected: 0 rows

-- Route count vs view (expected 5812 distinct origin/destination pairs)
SELECT COUNT(*) FROM (SELECT DISTINCT origin_airport_id, dest_airport_id FROM ontime.flight) r;
```

Reconciliation tolerances: counts exact; rates within ±0.0001 of the reference.

### 14.3 Reconciliation gotchas

- The `v_route_summary` view groups by `o.code` and **destination city**, so rows with a NULL origin code or a destination-only airport behave differently from the semantic query; compare route counts on airport ids, not codes.
- `ROUND(100.0 * AVG(...), 2)` in the views is a percentage with 2 decimals; semantic ratios are 0–1.

## 15. Repository Structure

```text
flight-ontime-nl-analytics/
├── data/                              # existing: ETL and specs (unchanged by this spec)
│   ├── README.md
│   ├── scripts/load.sh
│   ├── sql/01_schema.sql, 02_transform.sql
│   └── specs/
└── semantic-layer/                    # new, version-controlled, no DB credentials
    ├── README.md
    ├── pyproject.toml
    ├── definitions/
    │   ├── sources/postgres_sources.yml
    │   ├── semantic_models/flight_operations.yml
    │   ├── semantic_models/code_mappings.yml
    │   ├── metrics/flight_operations_metrics.yml
    │   ├── relationships/transport_relationships.yml
    │   └── policies/{data_policy,query_policy}.yml
    ├── docs/{source-inventory.md, metric-catalog.md, data-quality.md, change-management.md}
    ├── validation/{key_uniqueness.sql, referential_integrity.sql, flag_integrity.sql, metric_reconciliation.sql}
    ├── src/semantic_layer/{loader.py, validator.py, compiler.py, pg_client.py, policy.py}
    └── tests/
```

Everything under `semantic-layer/` is Git-tracked. Secrets are supplied only through environment variables or a secret manager.

## 16. Versioning, Change Management and CI

### 16.1 Breaking changes

Changing a metric's numerator, denominator, filters or null handling; changing the time basis; renaming/removing a metric or dimension; changing the meaning of a coded value; or changing a join is a **breaking change** and requires a new version (`arrival_delay_rate_v2`) with the old version `deprecated` for an agreed migration period.

### 16.2 Pull-request requirements

Each PR must state what changed, why, which metrics/dimensions are affected, the validation SQL result, the reconciliation result, the business owner approval for semantic changes, and any open questions from Section 7.3.

### 16.3 CI checks

```bash
python -m semantic_layer.validator --definitions definitions/ --check-references --check-policies --check-metric-catalog docs/metric-catalog.md
python -m semantic_layer.validator --definitions definitions/ --run-validation-sql --sql-dir validation/ --connection env   # read-only role
pytest tests/
```

The pipeline must fail on: YAML/JSON that does not parse; a metric referencing an unknown source, column, dimension or relationship; a metric missing mandatory attributes; an `approved` object without validation and reconciliation results; a physical column in a definition that no longer exists in the database; any SQL in validation files other than `SELECT`/`WITH`; and any credential or connection string found in the repository.

CI must run read-only validation only, using the least-privilege role in Section 4.3.

## 17. Implementation Plan

### Milestone 0: Repository and secure read-only access

- Create `semantic-layer/` and tooling; read connection via environment variables; no credentials committed.
- Enforce read-only session settings; confirm that a write attempt fails.
- **Acceptance:** one `SELECT` against `ontime.flight` succeeds; `CREATE TABLE`/`INSERT` is rejected.

### Milestone 1: Source documentation (largely complete)

- Section 6 inventory reviewed with data owners; Q1–Q7 raised.
- `docs/source-inventory.md` and `docs/data-quality.md` created from Sections 6–7.
- **Acceptance:** every column in `ontime.*` is either modeled or listed in `excluded_columns` with a reason.

### Milestone 2: First semantic model

- Author `flight_operations.yml`, sources, relationships, mappings and policies from Section 9–12.
- All objects `draft` or `validated`; validation SQL passes.

### Milestone 3: First approved metrics

- Business owners approve: `flight_count`, `cancelled_flight_count`, `cancellation_rate`, `diverted_flight_count`, `arrival_delay_rate`, `departure_delay_rate`, `on_time_arrival_rate` (after Q1–Q3).
- All reconcile per Section 14.

### Milestone 4: Query compiler and service readiness

- Parameterized PostgreSQL compiler, policy enforcement, audit logging.
- Contract tests for the future LLM intent parser and Streamlit app.
- **Acceptance:** unsupported/clarification responses for requests in Section 10.2 and for ambiguity Q1/Q4.

### Milestone 5: Data growth

- When more BTS months are loaded, re-run discovery (6.6), enable `require_date_filter`, revisit month/quarter dimensions, and re-baseline Section 14.

## 18. Definition of Done

The semantic layer is complete for this domain when:

1. Every object in `ontime` used by consumers is documented, classified and owned.
2. Every approved metric has a business definition, explicit numerator, denominator and null handling, allowed dimensions, validation SQL, and a passing reconciliation.
3. Joins exist only as listed relationships; airport roles are always explicit.
4. No credentials are stored in the repository and all database access is read-only and enforced at the session and role level.
5. Ambiguities Q1–Q7 are resolved or encoded as clarification/unsupported behavior.
6. CI validates definitions, references, policies and read-only SQL.

## 19. Minimal First Release

1. Create the `semantic-layer/` repository folder and read-only connection.
2. Model `flight_operations` with dimensions `flight_date`, `day_of_week`, `carrier_code`, `origin_airport_code`, `destination_airport_code`.
3. Release metrics: `flight_count`, `cancelled_flight_count`, `cancellation_rate`, `arrival_delay_rate`.
4. Validate and reconcile against the baselines in Section 14.
5. Obtain business sign-off, then mark approved.

Do not publish delay-minute, passenger, revenue, load-factor or tail-number metrics: the underlying data does not exist in `tran-stats`.
