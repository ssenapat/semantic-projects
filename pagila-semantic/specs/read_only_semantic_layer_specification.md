# Read-Only DVD Rental Semantic Layer Specification for PostgreSQL (`pagila`)

## 1. Purpose

This specification defines a metadata-driven, read-only semantic layer for the existing PostgreSQL database `pagila`. Pagila is the PostgreSQL port of the Sakila DVD-rental sample database (source: https://github.com/devrimgunduz/pagila, cloned locally at `semantic-projects/pagila`). It was loaded from `pagila-schema.sql` and `pagila-data.sql` into a local PostgreSQL 18.6 server (Docker container `pg-local`) with the `pgvector` 0.8.7 extension.

The semantic layer sits outside the database, in version-controlled metadata. It translates business questions about rentals, payments, customers, films and stores into approved, parameterised, read-only SQL. PostgreSQL remains the source of data and the query execution engine. The layer never creates, alters or writes to any database object.

The semantic layer is the single source of truth for business definitions: which measures exist, how they are calculated, which dimensions may be used, and how entities join.

## 2. Objectives

1. Provide a governed business vocabulary over `pagila` (entities, dimensions, measures, relationships).
2. Compile structured semantic requests (measures + dimensions + filters) into safe, read-only SQL.
3. Make data-quality caveats of this dataset explicit so that answers are correct (Section 7).
4. Keep all definitions in Git-managed YAML and documentation, validated against the live database (Section 12).
5. Serve LLM intent parsers, BI front-ends and an API, none of which write raw SQL against physical tables.

## 3. Scope

In scope:

- Semantic definitions for the `public` schema of `pagila`: facts (`rental`, `payment`), dimensions (`customer`, `film`, `category`, `actor`, `language`, `store`, `staff`, `address`, `city`, `country`) and the bridge tables.
- Business glossary, data-quality rules and ambiguity rules (Sections 7, 8).
- Metric, dimension, relationship and filter definitions (Section 9).
- Validation and reconciliation SQL (Section 12).
- Repository layout and the future query compiler (Sections 13 to 15).

Out of scope:

- Any change to the database: no DDL, no DML, no new views, roles or indexes created by the layer.
- ETL, forecasting, recommendation models, and any use of `film_embedding` vectors beyond describing the table (Section 6.2).
- Changing the Pagila data to fix its quirks. Quirks are documented and handled in definitions.

## 4. Connection and Security

### 4.1 Connection

| Item | Value |
|---|---|
| Engine | PostgreSQL 18.6 (Docker container `pg-local`) |
| Host | `PAGILA_PG_HOST` (local default `localhost`) |
| Port | `PAGILA_PG_PORT` (local default `5432`) |
| Database | `pagila` |
| Schema | `public` |
| User | `PAGILA_PG_USER` |
| Password | `PAGILA_PG_PASSWORD`, environment variable only |

The password and any credential must never appear in a repository file, a definition file, a log or a prompt. Definition files reference environment variable names only.

### 4.2 Allowed and prohibited operations

Allowed: a single `SELECT` statement (optionally starting with `WITH`) against the objects listed in Section 6.

Prohibited: `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `TRUNCATE`, `COPY`, `CREATE`, `ALTER`, `DROP`, `GRANT`, `REVOKE`, `CALL`, `DO`, `SET ROLE`, `SELECT ... INTO`, `SELECT ... FOR UPDATE`, multiple statements, and any function with side effects (for example `pg_terminate_backend`, `set_config`, `lo_*`, `nextval`, `setval`).

### 4.3 Enforced read-only session (defence in depth)

Every connection opened by the layer must run `SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY`, set `statement_timeout` (default 30 s), set `search_path = public`, and use a bounded `LIMIT` on detail queries (default 1,000 rows, hard cap 10,000).

This is a local sample database and the layer connects as the local `postgres` superuser. A dedicated `SELECT`-only role is not required (owner decision). The read-only session setting and the SQL guard (Section 14.3) are therefore the only protection, and the layer must log a warning at start-up that it is running with a privileged role. If the layer is ever pointed at a non-local database, this decision must be revisited.

## 5. Target Architecture

```text
Git repository (YAML definitions + docs + validation SQL)
        |  loads / validates / compiles
        v
Python semantic-layer service (future)  --- read-only SELECT --->  PostgreSQL `pagila`, schema `public`
        ^
LLM intent parser / Streamlit / API (consumers send semantic requests, never SQL)
```

Rule: consumers send metric, dimension and filter names. Only the compiler produces SQL, and it only emits tables, columns and join paths declared in the definitions.

## 6. Source Inventory (verified 2026 against the loaded database)

### 6.1 Objects exposed to the semantic layer

| Object | Type | Rows | Role |
|---|---|---|---|
| `rental` | table | 51,805 | Fact: one row per rental of one inventory item by one customer |
| `payment` | partitioned table (55 monthly partitions `payment_p2022_01` to `payment_p2026_07`) | 51,061 | Fact: one row per payment |
| `inventory` | table | 4,581 | Dimension/bridge: one physical copy of a film held by a store |
| `film` | table | 1,000 | Dimension |
| `film_category` | table | 2,367 | Bridge film to category (many per film, see 7.4) |
| `category` | table | 16 | Dimension |
| `film_actor` | table | 5,462 | Bridge film to actor |
| `actor` | table | 200 | Dimension |
| `language` | table | 6 | Dimension |
| `customer` | table | 999 | Dimension |
| `store` | table | 500 | Dimension (only stores 1 and 2 hold inventory, see 7.5) |
| `staff` | table | 1,500 | Dimension |
| `address` | table | 1,003 | Geography |
| `city` | table | 600 | Geography |
| `country` | table | 109 | Geography |

Pagila's own views (`actor_info`, `customer_list`, `film_list`, `nicer_but_slower_film_list`, `sales_by_film_category`, `sales_by_store`, `staff_list`) and the `rental_by_category` materialised view are not queried by consumers. They are used only as reconciliation references (Section 12).

Not exposed: `film_embedding` (1,000 rows, `vector(20)`), partition children of `payment` (always queried through the parent `payment`), the `film.fulltext` tsvector column, and `staff.password`, `staff.picture`, `staff.email`, `customer.email`, and address fields (`address`, `address2`, `phone`, `postal_code`, `district`) for customers and staff, which are personal data (Section 7.9).

### 6.2 Key columns

- `rental(rental_id PK, rental_date, inventory_id FK, customer_id FK, return_date NULL, staff_id FK, last_update)`
- `payment(payment_id, customer_id FK, staff_id FK, rental_id FK, amount numeric, payment_date)`, partitioned by `payment_date`
- `inventory(inventory_id PK, film_id FK, store_id FK)`
- `film(film_id PK, title, description NULL, release_year NULL, language_id FK, original_language_id NULL FK, rental_duration smallint, rental_rate numeric, length smallint NULL, length_hours generated, replacement_cost numeric, rating mpaa_rating NULL, special_features text[] NULL)`
- `mpaa_rating` enum: `G`, `PG`, `PG-13`, `R`, `NC-17`
- `customer(customer_id PK, store_id FK, first_name, last_name, email NULL, address_id FK, activebool boolean, create_date date, active integer NULL)`
- `staff(staff_id PK, first_name, last_name, address_id FK, email NULL, store_id FK, active boolean, ...)`
- `store(store_id PK, manager_staff_id FK, address_id FK)`
- `address(address_id PK, address, address2 NULL, district, city_id FK, postal_code NULL, phone)`; `city(city_id, city, country_id FK)`; `country(country_id, country)`

### 6.3 Declared constraints

Foreign keys exist for every join in Section 9.3 (all `ON UPDATE CASCADE ON DELETE RESTRICT`), including from each `payment` partition to `customer`, `rental` and `staff`. No `COMMENT ON` metadata exists in the database (0 comments), so all business descriptions live in this repository.

### 6.4 Data coverage

| Table | Date coverage |
|---|---|
| `rental.rental_date` | 2022-02-14 to 2026-07-28 |
| `payment.payment_date` | 2022-01-23 to 2026-07-28 |
| `customer.create_date` | 2022-01-02 to 2026-05-29 |

Total payment amount is 170,962.39. Payment amounts range from 0.00 to 11.99 (24 payments are exactly 0.00). Film rental rates range from 0.99 to 4.99, lengths from 46 to 185 minutes, replacement costs from 9.99 to 29.99, rental durations from 3 to 7 days, and release years from 2006 to 2024.

### 6.5 Re-discovery

The data can change when the database is reloaded, so all numbers in this document are a baseline, not a contract. Use read-only queries against `information_schema.columns`, `pg_catalog.pg_constraint`, `pg_partitioned_table` and `COUNT(*)` to refresh them, and re-run Section 12 after any reload. Detail queries must be bounded with `LIMIT`.

## 7. Domain Interpretation Rules

### 7.1 Core terms

| Term | Definition |
|---|---|
| Rental | One row in `rental`: one customer renting one inventory copy at `rental_date`. |
| Returned rental | A rental with `return_date IS NOT NULL`. |
| Open rental | A rental with `return_date IS NULL` (241 in the baseline). |
| Rental duration (actual) | `return_date - rental_date`, only for returned rentals (baseline range about 1.5 hours to 10.5 days). |
| Payment | One row in `payment`. Revenue is the sum of `payment.amount`. |
| Revenue | Sum of `payment.amount`, attributed to the date of the rental it pays for (`rental.rental_date`), see 7.2. |
| Active customer | `customer.activebool = true`. |
| Store | The store that holds the inventory item rented (`inventory.store_id`), see 7.5. |

### 7.2 Revenue and rentals are different facts

`rental` and `payment` are two fact tables and must never be joined and summed carelessly.

- Revenue questions use `payment.amount`. Time defaults to the **rental date** of the paid rental (`payment` joined to `rental` on `rental_id`). This is an owner decision, see 7.2.1.
- Rental-count questions use `rental`. Time defaults to `rental_date`.
- A question that mixes them (for example "revenue per rental") must compute each measure at its own grain and combine them only after aggregation, or via the declared `rental` to `payment` join with a distinct count (Section 7.3).

### 7.2.1 Date choice for revenue (decided)

`payment_date` does not line up with the rentals it pays for. Measured on the 51,061 payments joined to their rental:

| Check | Result |
|---|---|
| Paid on the same calendar day as the rental | 64 (0.1%) |
| Paid **before** the rental started | 14,665 (28.7%) |
| Paid after the rental was returned | 1,017 |
| Lag from rental to payment | -211 days to +162 days, median about 3.9 days |
| First month with payments | 2022-01, but the first rental is on 2022-02-14 |

A payment dated before its own rental is not meaningful for a revenue timeline, so the options were:

| Option | Meaning | Verdict |
|---|---|---|
| A. `rental_date` of the paid rental | Revenue belongs to the month the film was rented | **Chosen.** Consistent with rental counts, so revenue per rental and trends line up. |
| B. `payment_date` | Cash-received view | Kept as an explicit, optional dimension (`payment_date`), never the default. Use only when asked for "payments received". |
| C. `return_date` | Revenue when the rental ended | Not used. Open rentals have no return date. |

Consequences:

- Revenue by month is the sum of payments for rentals made that month. Rentals with no payment contribute 0.
- The `payment` to `rental` join is safe for this purpose: there are no orphan payments, and a payment has exactly one rental, so it does not duplicate payment rows.
- Partition pruning on `payment_date` no longer applies by default. At 51,061 rows this has no practical cost.
- An answer based on `payment_date` must say so in its caveats.

### 7.3 Payments and rentals do not match one to one

- 749 rentals have no payment, so revenue by rental-based attributes (film, category, store) understates activity for those rentals.
- One `rental_id` has more than one payment row.
- 5 payments belong to a customer that differs from the customer on the rental.
- Consequence: counting rentals after joining to `payment` multiplies rows. Use `COUNT(DISTINCT rental_id)` for rental counts on any query that joins `payment`, and `SUM(payment.amount)` only at the payment grain.
- For "customer" attributes on revenue, use `payment.customer_id`. For "customer" on rentals, use `rental.customer_id`.

### 7.4 Films have several categories

`film_category` has 2,367 rows for 1,000 films, and 964 films belong to more than one category. Classic Sakila has one category per film, so this dataset differs.

**Decision: allocate equally.** A film with `n` categories contributes `1/n` of each rental, payment and revenue amount to each of its categories. Every film has at least one category (0 uncategorised films in the baseline), so category totals add up exactly to the grand total.

- Allocation weight per film and category: `1.0 / COUNT(*) OVER (PARTITION BY film_id)` computed from `film_category`. It is defined once as the derived source `film_category_weight` and applied by the compiler whenever the `category` dimension is used.
- With the `category` dimension, `rental_count` becomes the sum of weights and is fractional (for example 0.33 rentals). `total_revenue` is the sum of `amount * weight`. The answer must label these as "allocated".
- Without the `category` dimension nothing is weighted, and the plain counts are used.
- A film with no category (none today) is reported under the label `Uncategorised` with weight 1.
- Reconciliation: the sum over all categories must equal the grand total to within rounding. This is a mandatory check (Section 12).
- Pagila's own `sales_by_film_category` view counts each film fully in every category, so its numbers will legitimately be higher than the allocated ones. That view is for comparison only and is not expected to match.

### 7.5 Only two stores hold inventory

`store` has 500 rows and `staff` has 1,500, but all 4,581 inventory items belong to stores 1 (2,270 items) and 2 (2,311 items). Rentals split 25,761 and 26,044 between them. Customers belong to stores 1 (526) and 2 (473) only.

- "Store" for rentals and revenue means `inventory.store_id` via `rental.inventory_id`.
- Stores 3 to 500 (498 stores) have no inventory and no rentals. **Decision: they are test data.** The store dimension exposes only stores that hold inventory (1 and 2), and the compiler must never list the others, not even with zero values.
- The same applies to staff: only staff 1 and 2 appear on any rental or payment, and the other 1,498 staff members are test data. The staff dimension exposes only staff who appear on at least one rental or payment.
- Stores and staff are exposed through those filters, defined once in the sources file as `store_in_use` and `staff_in_use` and re-evaluated at validation time (Section 12).
- `staff.store_id` does not match the rented inventory's store for rentals (every rental has a staff member whose store differs from the inventory's store, and only 2 distinct staff members appear on rentals). Never derive store from `rental.staff_id`.

### 7.6 Customer status

`customer.activebool` is the active flag (987 true in the baseline). `customer.active` is a legacy integer (966 with 1, 33 with 0) and the two disagree. The layer uses `activebool` and does not expose `active`.

### 7.7 Nullable fields

`film.rating`, `film.release_year`, `film.length`, `film.description`, `film.original_language_id`, `film.special_features`, `rental.return_date`, `customer.email` and `address.address2` and `address.postal_code` can be NULL. Rules:

- Group-by on a nullable column shows NULL as the label `Unknown`.
- Averages ignore NULL and the answer must state the number of rows used when it is lower than the population.
- Open rentals are excluded from rental-duration measures.

### 7.8 Time handling

All timestamps are `timestamp with time zone`. The layer evaluates dates in UTC unless the request names a time zone. `payment` is partitioned by `payment_date`, but revenue is dated by rental date (7.2.1), so partition pruning only happens when a request explicitly filters on `payment_date`. Data stops at 2026-07-28, so the last month (2026-07) is partial and the last partition that exists is `payment_p2026_07`. Relative dates ("last month") resolve against the latest date in the data unless the request says to use today.

### 7.9 Personal data

**Decision: customer names are available to all consumers**, with no role restriction. Customer and staff names are available as dimensions, and grouping by `customer_id` plus name is allowed. Email, phone, street address, postal code, `staff.password` and `staff.picture` are still never returned, and detail-level exports of those columns are not allowed. Customer city and country remain available as geography dimensions.

### 7.10 Ambiguous terms (need confirmation, or a default that must be stated in the answer)

| Question | Default |
|---|---|
| "Sales", "revenue", "income" | Sum of `payment.amount` by the paid rental's `rental_date` (7.2.1). |
| "Rentals", "transactions" | Count of `rental` rows by `rental_date`. |
| "Best", "top" | Rank by revenue for customers, stores and categories, and by rental count for films and actors. Say which. |
| "Popular film" | Rental count (distinct `rental_id`). |
| "Late" or "overdue" | **Proposed default, not yet confirmed by the business:** a returned rental is late when `return_date - rental_date > film.rental_duration` days (the standard Sakila meaning of `rental_duration`). Open rentals are not counted as late. Always state the rule. See 7.10.1. |
| "Customer" count | Distinct `customer_id` with the stated activity in the period. |
| "Genre" | `category.name`, subject to 7.4. |
| "Language" | `film.language_id` (spoken language of the film), not original language. |
| "Revenue by film or category" | Revenue of payments whose rental is for that film or category (7.3 and 7.4 apply). |

### 7.10.1 Late-return rule (proposed)

No business rule for lateness was supplied, so this is a proposal. `film.rental_duration` (3 to 7 days) is the number of days a film may be kept, so a returned rental is late when the actual time out exceeds it. Measured with that rule on the baseline: 27,750 of 51,564 returned rentals (53.8%) are late. A rate that high suggests the data was generated randomly rather than from real behaviour, so late-return figures should be treated as illustrative. Until the business owner approves a rule, `late_return_count` stays in `draft`, is not exposed to consumers as a validated measure, and any answer using it must show the rule and this caveat. Other rules that can be adopted instead: a grace period (for example one extra day), measuring in whole days instead of elapsed time, or counting open rentals older than their duration.

## 8. Semantic Model Standards

### 8.1 Definition states

`draft` (written, not yet validated), `validated` (checked against the database and reconciled), `approved` (business owner sign-off), `deprecated`. Only `validated` and `approved` objects may be exposed to consumers. No object moves to `validated` without a recorded validation query result.

### 8.2 Naming

Lowercase `snake_case`, singular domain nouns (`rental`, `payment`, `customer`). Metrics are named by what they measure (`total_revenue`, `rental_count`). Physical identifiers are always quoted by the compiler and validated against an allow-list.

### 8.3 Mandatory metadata for every definition

`name`, `label`, `description`, `status`, `version`, `business_owner`, `technical_owner`, `source`, `grain`, `expression`, `filters`, `allowed_dimensions`, `unit`, `null_handling`, `validation_sql` and `change_log`.

## 9. Semantic Model Definitions

### 9.1 Sources (`definitions/sources/pagila_sources.yml`)

```yaml
connection:
  engine: postgresql
  database: pagila
  schema: public
  credentials: env        # PAGILA_PG_HOST, PAGILA_PG_PORT, PAGILA_PG_USER, PAGILA_PG_PASSWORD
sources:
  - { name: rental,        physical_object: public.rental,        object_type: table, grain: one row per rental,                  primary_key: [rental_id] }
  - { name: payment,       physical_object: public.payment,       object_type: partitioned_table, grain: one row per payment,     primary_key: [payment_id, payment_date], partition_key: payment_date }
  - { name: inventory,     physical_object: public.inventory,     object_type: table, grain: one row per physical film copy,      primary_key: [inventory_id] }
  - { name: film,          physical_object: public.film,          object_type: table, grain: one row per film,                    primary_key: [film_id] }
  - { name: film_category, physical_object: public.film_category, object_type: table, grain: one row per film and category,      primary_key: [film_id, category_id] }
  - { name: category,      physical_object: public.category,      object_type: table, grain: one row per category,                primary_key: [category_id] }
  - { name: film_actor,    physical_object: public.film_actor,    object_type: table, grain: one row per film and actor,          primary_key: [actor_id, film_id] }
  - { name: actor,         physical_object: public.actor,         object_type: table, grain: one row per actor,                   primary_key: [actor_id] }
  - { name: language,      physical_object: public.language,      object_type: table, grain: one row per language,                primary_key: [language_id] }
  - { name: customer,      physical_object: public.customer,      object_type: table, grain: one row per customer,                primary_key: [customer_id] }
  - { name: store,         physical_object: public.store,         object_type: table, grain: one row per store,                   primary_key: [store_id] }
  - { name: staff,         physical_object: public.staff,         object_type: table, grain: one row per staff member,            primary_key: [staff_id] }
  - { name: address,       physical_object: public.address,       object_type: table, grain: one row per address,                 primary_key: [address_id] }
  - { name: city,          physical_object: public.city,          object_type: table, grain: one row per city,                    primary_key: [city_id] }
  - { name: country,       physical_object: public.country,       object_type: table, grain: one row per country,                 primary_key: [country_id] }
derived_sources:          # defined once, read-only, expressed as SELECT fragments by the compiler (nothing is created in the database)
  - name: film_category_weight
    description: equal allocation of a film across its categories (7.4)
    sql: "SELECT film_id, category_id, 1.0 / COUNT(*) OVER (PARTITION BY film_id) AS weight FROM public.film_category"
  - name: store_in_use
    description: stores that hold inventory, the only stores exposed (7.5)
    sql: "SELECT s.* FROM public.store s WHERE EXISTS (SELECT 1 FROM public.inventory i WHERE i.store_id = s.store_id)"
  - name: staff_in_use
    description: staff who appear on at least one rental or payment, the only staff exposed (7.5)
    sql: "SELECT st.* FROM public.staff st WHERE EXISTS (SELECT 1 FROM public.rental r WHERE r.staff_id = st.staff_id) OR EXISTS (SELECT 1 FROM public.payment p WHERE p.staff_id = st.staff_id)"
reference_only:           # never queried by consumers, used for reconciliation (Section 12)
  - { name: ref_sales_by_store,         physical_object: public.sales_by_store,         object_type: view }
  - { name: ref_sales_by_film_category, physical_object: public.sales_by_film_category, object_type: view }
  - { name: ref_rental_by_category,     physical_object: public.rental_by_category,     object_type: materialized_view }
```

### 9.2 Semantic models (`definitions/semantic_models/*.yml`)

Two models, because rentals and payments are separate facts (7.2).

```yaml
semantic_models:
  - name: rental_activity
    status: draft
    fact: rental
    grain: one row per rental
    time_dimension: rental_date
    entities:
      - { name: rental,   type: primary, column: rental_id }
      - { name: customer, type: foreign, column: customer_id, references: customer.customer_id }
      - { name: inventory,type: foreign, column: inventory_id, references: inventory.inventory_id }
    dimensions:
      - { name: rental_date,   label: Rental Date, type: time, column: rental_date, granularities: [day, week, month, quarter, year] }
      - { name: rental_hour,   label: Rental Hour, type: categorical, expression: "EXTRACT(hour FROM rental_date)" }
      - { name: day_of_week,   label: Day of Week, type: categorical, expression: "EXTRACT(isodow FROM rental_date)", values: "1 Monday to 7 Sunday" }
      - { name: is_returned,   label: Returned,    type: categorical, expression: "return_date IS NOT NULL" }
      - { name: store_id,      label: Store,       type: categorical, source: inventory, column: store_id, note: "store of the rented copy, see 7.5" }
      - { name: film_title,    label: Film,        type: categorical, source: film, column: title }
      - { name: rating,        label: MPAA Rating, type: categorical, source: film, column: rating, null_policy: label_unknown }
      - { name: film_language, label: Film Language, type: categorical, source: language, column: name }
      - { name: category,      label: Category,    type: categorical, source: category, column: name, allocation: equal, weight_source: film_category_weight, note: "measures are multiplied by weight, 7.4" }
      - { name: customer_name, label: Customer,    type: categorical, source: customer, expression: "first_name || ' ' || last_name" }
      - { name: customer_country, label: Customer Country, type: categorical, source: country, column: country, path: customer>address>city>country }
      - { name: customer_city, label: Customer City, type: categorical, source: city, column: city, path: customer>address>city }
    measures:
      - { name: rental_count,        label: Rentals, aggregation: count_distinct, expression: rental_id, unit: count, status: draft }
      - { name: returned_count,      label: Returned Rentals, aggregation: count_distinct, expression: rental_id, filter: "return_date IS NOT NULL", unit: count, status: draft }
      - { name: open_rental_count,   label: Open Rentals, aggregation: count_distinct, expression: rental_id, filter: "return_date IS NULL", unit: count, status: draft }
      - { name: active_customer_count, label: Renting Customers, aggregation: count_distinct, expression: customer_id, unit: count, status: draft }
      - { name: avg_rental_days,     label: Average Rental Days, aggregation: avg, expression: "EXTRACT(epoch FROM (return_date - rental_date)) / 86400.0", filter: "return_date IS NOT NULL", unit: days, status: draft }
      - { name: late_return_count,   label: Late Returns, aggregation: count_distinct, expression: rental_id, filter: "return_date IS NOT NULL AND (return_date - rental_date) > film.rental_duration * interval '1 day'", requires: [film], unit: count, status: draft, expose: false, note: "proposed rule, not approved; stays draft and unexposed until the owner confirms, 7.10.1" }

  - name: payment_activity
    status: draft
    fact: payment
    grain: one row per payment
    time_dimension: rental_date          # decided, 7.2.1
    entities:
      - { name: payment,  type: primary, column: payment_id }
      - { name: customer, type: foreign, column: customer_id, references: customer.customer_id }
      - { name: rental,   type: foreign, column: rental_id,   references: rental.rental_id, required_join: true }
    dimensions:
      - { name: rental_date,   label: Rental Date (default revenue date), type: time, source: rental, column: rental_date, path: payment>rental, granularities: [day, week, month, quarter, year], default: true }
      - { name: payment_date,  label: Payment Date (cash received), type: time, column: payment_date, granularities: [day, week, month, quarter, year], default: false, note: "optional, 7.2.1; filtering on it enables partition pruning" }
      - { name: payment_customer, label: Customer, type: categorical, source: customer, expression: "first_name || ' ' || last_name" }
      - { name: payment_staff, label: Staff Member, type: categorical, source: staff_in_use, expression: "first_name || ' ' || last_name" }
      - { name: payment_store, label: Store, type: categorical, source: store_in_use, column: store_id, path: payment>rental>inventory, note: "unpaid rentals drop out, 7.3" }
      - { name: payment_film,  label: Film, type: categorical, source: film, column: title, path: payment>rental>inventory>film }
      - { name: payment_category, label: Category, type: categorical, source: category, column: name, allocation: equal, weight_source: film_category_weight, note: "amount is multiplied by weight, 7.4" }
    measures:
      - { name: total_revenue,    label: Total Revenue, aggregation: sum, expression: amount, unit: currency, status: draft }
      - { name: payment_count,    label: Payments, aggregation: count, expression: payment_id, unit: count, status: draft }
      - { name: avg_payment,      label: Average Payment, aggregation: avg, expression: amount, unit: currency, status: draft }
      - { name: paying_customer_count, label: Paying Customers, aggregation: count_distinct, expression: customer_id, unit: count, status: draft }
      - { name: zero_payment_count, label: Zero-Value Payments, aggregation: count, expression: payment_id, filter: "amount = 0", unit: count, status: draft }
```

Derived measures (declared separately in `definitions/metrics/`):

- `revenue_per_rental` = `total_revenue` divided by `rental_count`. Both are dated by `rental_date`, so they line up by period and store. The answer must state that 749 rentals are unpaid and that they count in the denominator.
- `revenue_per_customer` = `total_revenue` divided by `paying_customer_count`.
- `revenue_growth_pct` = month-on-month change in `total_revenue`, with the partial last month flagged (7.8).

### 9.3 Relationships (`definitions/relationships/pagila_relationships.yml`)

```yaml
relationships:
  - { from: rental.inventory_id,   to: inventory.inventory_id,   type: many_to_one, cardinality: required }
  - { from: rental.customer_id,    to: customer.customer_id,     type: many_to_one }
  - { from: rental.staff_id,       to: staff.staff_id,           type: many_to_one }
  - { from: payment.rental_id,     to: rental.rental_id,         type: many_to_one, cardinality: optional_on_rental_side, note: "749 rentals have no payment" }
  - { from: payment.customer_id,   to: customer.customer_id,     type: many_to_one }
  - { from: payment.staff_id,      to: staff.staff_id,           type: many_to_one }
  - { from: inventory.film_id,     to: film.film_id,             type: many_to_one }
  - { from: inventory.store_id,    to: store.store_id,           type: many_to_one }
  - { from: film.language_id,      to: language.language_id,     type: many_to_one }
  - { from: film_category.film_id, to: film.film_id,             type: many_to_one }
  - { from: film_category.category_id, to: category.category_id, type: many_to_one, allocation: equal, weight_source: film_category_weight }
  - { from: film_actor.film_id,    to: film.film_id,             type: many_to_one }
  - { from: film_actor.actor_id,   to: actor.actor_id,           type: many_to_one, fan_out: true }
  - { from: customer.address_id,   to: address.address_id,       type: many_to_one }
  - { from: customer.store_id,     to: store.store_id,           type: many_to_one }
  - { from: store.address_id,      to: address.address_id,       type: many_to_one }
  - { from: address.city_id,       to: city.city_id,             type: many_to_one }
  - { from: city.country_id,       to: country.country_id,       type: many_to_one }
```

Join rules:

- The category relationship is **allocated equally** (7.4): the compiler multiplies each measure by `film_category_weight.weight`, so totals are preserved.
- The actor relationship (`film_actor`) is still a plain `fan_out`: it multiplies fact rows, a film counts fully for each of its actors, and actor totals do not add up to the grand total. A query may include at most one fan-out dimension unless the request explicitly accepts it, and the answer must carry that caveat. A query may not combine the category and actor dimensions in one request.
- Only declared paths may be used. The compiler never invents a join and never uses a cross join.
- A query that groups by `film`, `category` or `store` from the `payment` model goes through `rental` and `inventory`, so unpaid rentals disappear from the result (7.3).

### 9.3.1 Combining measures

Every measure carries a `combinable_with` tag naming its fact group (initially `rental_activity` or `payment_activity`). Two measures can be requested together only when their tags match; this is a group tag, not a pairwise list. A ratio or average measure is computed from its own numerator and denominator per group, never from other columns in the same query. The compiler computes all measures in one `SELECT` with the declared joins, so no measure is multiplied by another's join.

### 9.4 Business glossary (`docs/glossary.md`)

Each term in 7.1 and 7.10 is recorded with its approved definition, owner and last review date.

## 10. Semantic Request Contract

A request is structured JSON, never SQL:

```json
{
  "model": "payment_activity",
  "measures": ["total_revenue", "payment_count"],
  "dimensions": ["rental_date:month", "payment_store"],
  "filters": [{"dimension": "rental_date", "op": "between", "value": ["2025-01-01", "2025-12-31"]}],
  "order_by": [{"field": "total_revenue", "direction": "desc"}],
  "limit": 100
}
```

### 10.1 Several measures and several dimensions

One request may ask for several measures and several dimensions. `measures` is a list of 1 to `max_measures_per_query` unique measures (default 5); `dimensions` is a list of 0 to 8 unique entries, and each entry may appear only once. The legacy single-measure field is removed (no compatibility alias): a request with `measure` is rejected with a validation error. An unknown field is always rejected.

Rules, applied in this order (any failure returns a clarification or an "unsupported" outcome, never a guess):

1. Measures are unique. Every measure exists and has status `validated`.
2. All measures in one request must belong to the same semantic model, so they share the same fact grain and the same date basis. Rental measures and payment measures are separate facts (7.2) and are never combined in one request; the response says which model each group belongs to, so the user can ask twice.
3. Each dimension and each filter dimension must be allowed for every requested measure. The allowed set is the intersection of the `allowed_dimensions` of all requested measures. If a dimension is allowed for some measures but not all, the request is "unsupported", naming the blocking measures.
4. A date dimension's grain (for example `rental_date:month`) must be one of the grains allowed for every measure.
5. Fan-out rules (7.3, 7.4, 9.3) and the limit of 1 fan-out dimension (14.2) apply to the request as a whole. The category weight (7.4) is applied to every measure in the request, so all measures stay consistent with each other.
6. Ambiguous terms (7.10) that match more than one dimension (for example "store" as the rental store or the payment store) produce a clarification only when the semantic model is not yet determined; once the measures fix the model (revenue means `payment_activity`), the term resolves within that model ("store" becomes `payment_store`). Never a silent choice between models.
7. `order_by` may name any requested measure or dimension. Default order: first measure descending, then each dimension ascending, with NULLs last.
8. `limit` applies to the whole result set; the result is truncated deterministically and the response sets `truncated: true`.

The response includes the data, the SQL that ran, the definitions and versions used, the row count and every caveat triggered (actor fan-out, category allocation, unpaid rentals, partial month, null labels, personal data suppression, use of `payment_date`).

For several measures the response is one row per distinct combination of dimension values, with one column per dimension and one column per measure. Each column carries its role (`dimension` or `measure`), label, unit and data type; there is a list of `measure_versions` (one entry per measure); and `truncated` and `row_count` are at the top level. The response is read-only, deterministic (same request, same order, same truncation) and audited.

## 11. Example Business Questions

| Question | Model, measures, dimensions |
|---|---|
| Revenue by month | `payment_activity`: `total_revenue` by `rental_date:month` |
| Cash received by month (explicit request only) | `payment_activity`: `total_revenue` by `payment_date:month`, with a caveat |
| Top 10 films by rentals | `rental_activity`: `rental_count` by `film_title`, order desc, limit 10 |
| Revenue by store | `payment_activity`: `total_revenue` by `payment_store` |
| Rentals by category (equal allocation, fractional counts that sum to the total) | `rental_activity`: `rental_count` by `category` |
| Revenue by category (equal allocation) | `payment_activity`: `total_revenue` by `payment_category` |
| Average rental duration by rating | `rental_activity`: `avg_rental_days` by `rating` |
| Open rentals per store | `rental_activity`: `open_rental_count` by `store_id` |
| Top customers by revenue | `payment_activity`: `total_revenue` by `payment_customer`, limit 10 |
| Revenue by customer country | `payment_activity` with `customer_country` path |
| Revenue and payment count by month | `payment_activity`: `total_revenue`, `payment_count` by `rental_date:month` |
| Rentals and average rental days by category and rating | `rental_activity`: `rental_count`, `avg_rental_days` by `category`, `rating` |
| Revenue by store and category (equal allocation) | `payment_activity`: `total_revenue` by `payment_store`, `payment_category` |
| Revenue and rentals together by month | Clarification or "unsupported": measures from different models cannot be combined (10.1 rule 2); ask as two questions |
| Late returns by category | Not available until the late-return rule is approved (7.10.1) |

Questions that need data this layer lacks (customer demographics, actor revenue per actor share, film profit, cost, staff pay) must be answered with "not supported", not guessed.

## 12. Validation and Reconciliation SQL

All checks are read-only `SELECT`s stored in `validation/` and run before any definition moves to `validated`.

| Check | SQL (summary) | Expected baseline |
|---|---|---|
| Row counts | `SELECT COUNT(*)` per exposed table | Section 6.1 |
| Revenue total | `SELECT SUM(amount) FROM payment` | 170,962.39 |
| Revenue by store matches Pagila | compare `payment_activity` revenue by `payment_store` with `sales_by_store.total_sales` | equal per store |
| Allocated category revenue adds up | sum of allocated revenue over all categories vs `SUM(payment.amount)` | equal within 0.01 |
| Allocated category rentals add up | sum of allocated rental counts over all categories vs `COUNT(*)` of rentals | equal within 0.01 |
| Allocation weights | for each film, weights sum to 1 | all films |
| Every film has a category | films with no `film_category` row | 0 |
| Category revenue vs Pagila (informational) | allocated revenue vs `sales_by_film_category`, which counts each film fully per category | allocated is lower or equal; not a failure |
| Stores in use | `store_in_use` row count | 2 |
| Staff in use | `staff_in_use` row count | 2 |
| Revenue by rental month adds up | sum over months of revenue by `rental_date` vs `SUM(payment.amount)` | 170,962.39 |
| No orphan payments | payments with no matching rental | 0 |
| Unpaid rentals | rentals with no payment | 749 |
| Multi-payment rentals | `rental_id` with more than one payment | 1 |
| Payment/rental customer mismatch | payments whose customer differs from the rental's customer | 5 |
| Multi-category films | films with more than one category | 964 |
| Inventory stores | `SELECT DISTINCT store_id FROM inventory` | 1 and 2 |
| Open rentals | rentals with `return_date IS NULL` | 241 |
| Return before rental | rentals with `return_date < rental_date` | 0 |
| Partition coverage | partitions of `payment` | 55, `p2022_01` to `p2026_07` |
| Enum values | `film.rating` labels | G, PG, PG-13, R, NC-17 |
| Definition vs schema | every column in definitions exists in `information_schema.columns` | all found |

A reconciliation failure blocks promotion of the affected definition and is recorded in its `change_log`.

## 13. Future Query Compiler

1. Parse and validate the request against the definitions (unknown names are rejected), including the several-measure rules in 10.1.
2. Choose the semantic model, enforcing 7.2. All requested measures must resolve to the same model.
3. Resolve joins along declared paths and apply fan-out rules (9.3).
4. Add mandatory rules: the `payment` to `rental` join for revenue dated by rental date, the `store_in_use` and `staff_in_use` restrictions, and the category weight (7.2.1, 7.4, 7.5).
5. Emit one parameterised `SELECT` that computes every requested measure over the same joins and the same `GROUP BY` (one query, so measures never fan out or double count against each other), with quoted, allow-listed identifiers and bound parameters for every value. Aliases are the measure and dimension names. `ORDER BY` follows 10.1 rule 7 and `LIMIT` is applied as `min(requested_limit, max_rows)`; the compiler fetches one extra row to decide `truncated`.
6. Run the SQL guard (14.3), then execute in a read-only session with a timeout and a row limit.
7. Attach caveats and return.

## 14. Safety Controls

### 14.1 Identifier and value safety

Identifiers come only from definitions. Values are always bound parameters, never concatenated. Free text from users or LLMs is never placed into SQL.

### 14.2 Resource limits

Default 1,000 rows, hard cap 10,000, `statement_timeout` 30 s, maximum 10 joins per query, maximum 1 fan-out dimension (unless accepted), maximum 5 measures and 8 dimensions per request. The measure limit lives in one place, the policy definitions (`max_measures_per_query`), and is read by every service.

### 14.3 SQL guard

Before execution, parse the SQL and reject anything that is not exactly one `SELECT` or `WITH ... SELECT` statement, that references an object outside the Section 6.1 allow-list, or that contains a prohibited keyword or function from 4.2. Sensitive columns (7.9) are rejected even if requested.

### 14.4 Audit

Log request (the full list of measures and dimensions), definition versions (one per measure), final SQL, row count, duration and caller, never credentials or returned personal data.

## 15. Repository Layout and Versioning

```text
pagila-analytics/
  README.md
  specs/read_only_semantic_layer_specification.md
  definitions/
    sources/pagila_sources.yml
    semantic_models/{rental_activity,payment_activity}.yml
    relationships/pagila_relationships.yml
    metrics/derived_metrics.yml
  docs/glossary.md
  validation/*.sql
  semantic-layer/            # compiler and service (future)
  data/ semantic-query-service/ semantic-retrieval-service/ llm-intent-parser/ streamlit-analytics-client/
```

Definitions use semantic versioning. A change to a measure's meaning is a major version, an added dimension is a minor version, a fix to a label is a patch. Every change updates `change_log`, re-runs Section 12, and is reviewed by the technical and business owners.

## 16. Owner Decisions and Remaining Open Items

| # | Question | Decision |
|---|---|---|
| 1 | Default date for revenue | **Rental date** of the paid rental. `payment_date` is an optional, explicit dimension (7.2.1). |
| 2 | Category breakdowns | **Allocate equally** across a film's categories, so totals add up (7.4). |
| 3 | Late-return rule | **Not decided.** Proposed: returned rentals where `return_date - rental_date > film.rental_duration` days (7.10.1). `late_return_count` stays `draft` and unexposed until approved. |
| 4 | Stores 3 to 500 and the extra staff | **Test data.** Hidden from consumers (7.5). |
| 5 | Customer names | **Exposed to all consumers** (7.9). |
| 6 | Dedicated read-only role | **Not required** (4.3). |

| 7 | Several measures in one request | **Allowed** within one semantic model (10.1). Measures from different models are not combined. |
| 8 | Maximum measures per request | **5**, configured in one place (`max_measures_per_query`). |
| 9 | Legacy single-measure field | **Removed**, no compatibility alias. Requests using it are rejected. |
| 10 | "Store" and similar terms matching more than one dimension | **Clarification**, never a silent choice (10.1 rule 6). |
| 11 | Behaviour when `limit` truncates the result | Return the rows and set `truncated: true`. |

Remaining open item: approval of the late-return rule (item 3). Since actors are still a plain fan-out (9.3), a decision on whether actor breakdowns should also be allocated equally is optional and can be added later.

## 17. Source and Reproducibility

- Database source: https://github.com/devrimgunduz/pagila (`pagila-schema.sql`, `pagila-data.sql`), loaded into `pagila` on `pg-local` (PostgreSQL 18.6, `pgvector` 0.8.7 installed inside the container via `postgresql-18-pgvector`; this install is lost if the container is recreated from the plain `postgres` image).
- All counts in this specification were measured on the loaded database and must be re-verified after any reload (6.5).
