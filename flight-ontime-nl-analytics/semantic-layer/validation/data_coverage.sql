-- Data coverage checks (REQ-020). Read-only: every block is a single SELECT.
-- Block format: CONTRACT C-4.1. Expectations: CONTRACT C-4.2.

-- @check: date_coverage
-- @description: the load covers 2026-01-01 to 2026-01-31 with no missing day
-- @expect: {"first_date": "2026-01-01", "last_date": "2026-01-31", "distinct_dates": 31}
SELECT MIN(f.fl_date) AS first_date,
       MAX(f.fl_date) AS last_date,
       COUNT(DISTINCT f.fl_date) AS distinct_dates
FROM ontime.flight f;

-- @check: constant_period_columns
-- @description: quarter and month are constant in the current single-month load (warns when more months are loaded)
-- @expect: {"distinct_quarter": 1, "distinct_month": 1}
-- @severity: warn
SELECT COUNT(DISTINCT f.quarter) AS distinct_quarter,
       COUNT(DISTINCT f.month) AS distinct_month
FROM ontime.flight f;

-- @check: day_of_week_domain
-- @description: day_of_week is within 1 (Monday) to 7 (Sunday)
-- @expect: {"out_of_range": 0}
SELECT COUNT(*) FILTER (WHERE f.day_of_week IS NULL OR f.day_of_week NOT BETWEEN 1 AND 7) AS out_of_range
FROM ontime.flight f;
