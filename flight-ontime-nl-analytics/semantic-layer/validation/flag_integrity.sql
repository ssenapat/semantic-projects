-- Flag and cancellation-code integrity checks (REQ-020). Read-only: every block is a single SELECT.
-- Block format: CONTRACT C-4.1. Expectations: CONTRACT C-4.2.

-- @check: flag_domains
-- @description: cancelled, diverted, dep_del15 and arr_del15 only hold 0 or 1 (dep_del15 and arr_del15 may be NULL)
-- @expect: {"bad_cancelled": 0, "bad_diverted": 0, "bad_dep_del15": 0, "bad_arr_del15": 0}
SELECT COUNT(*) FILTER (WHERE f.cancelled IS NULL OR f.cancelled NOT IN (0, 1)) AS bad_cancelled,
       COUNT(*) FILTER (WHERE f.diverted IS NULL OR f.diverted NOT IN (0, 1)) AS bad_diverted,
       COUNT(*) FILTER (WHERE f.dep_del15 NOT IN (0, 1)) AS bad_dep_del15,
       COUNT(*) FILTER (WHERE f.arr_del15 NOT IN (0, 1)) AS bad_arr_del15
FROM ontime.flight f;

-- @check: cancellation_code_consistency
-- @description: cancellation_code is present exactly when cancelled = 1 (spec 14.1)
-- @expect: {"inconsistent_rows": 0}
SELECT COUNT(*) AS inconsistent_rows
FROM ontime.flight f
WHERE (f.cancelled = 1) <> (f.cancellation_code IS NOT NULL);

-- @check: cancellation_code_domain
-- @description: cancellation_code is one of A, B, C, D when present
-- @expect: {"unknown_codes": 0}
SELECT COUNT(*) AS unknown_codes
FROM ontime.flight f
WHERE f.cancellation_code IS NOT NULL
  AND f.cancellation_code NOT IN ('A', 'B', 'C', 'D');

-- @check: arr_flag_null_rule
-- @description: arr_del15 is NULL exactly when the flight was cancelled or diverted
-- @expect: {"violations": 0}
SELECT COUNT(*) AS violations
FROM ontime.flight f
WHERE (f.arr_del15 IS NULL) <> (f.cancelled = 1 OR f.diverted = 1);

-- @check: flag_combinations
-- @description: mutually exclusive flag combinations add up to the whole table, nothing else exists
-- @expect: {"completed": 517222, "diverted_not_cancelled": 1146, "cancelled_with_dep_flag": 406, "cancelled_without_dep_flag": 25229, "other_combinations": 0}
WITH combos AS (
  SELECT COUNT(*) AS total_rows,
         COUNT(*) FILTER (WHERE f.cancelled = 0 AND f.diverted = 0) AS completed,
         COUNT(*) FILTER (WHERE f.cancelled = 0 AND f.diverted = 1) AS diverted_not_cancelled,
         COUNT(*) FILTER (WHERE f.cancelled = 1 AND f.dep_del15 IS NOT NULL) AS cancelled_with_dep_flag,
         COUNT(*) FILTER (WHERE f.cancelled = 1 AND f.dep_del15 IS NULL) AS cancelled_without_dep_flag
  FROM ontime.flight f
)
SELECT k.completed,
       k.diverted_not_cancelled,
       k.cancelled_with_dep_flag,
       k.cancelled_without_dep_flag,
       k.total_rows - (k.completed + k.diverted_not_cancelled + k.cancelled_with_dep_flag + k.cancelled_without_dep_flag) AS other_combinations
FROM combos k;

-- @check: div_landings_anomaly
-- @description: non-diverted flights with diversion landings (known anomaly, owner decision pending)
-- @expect: {"non_diverted_with_landings": 120}
-- @severity: warn
SELECT COUNT(*) FILTER (WHERE f.diverted = 0 AND f.div_airport_landings > 0) AS non_diverted_with_landings
FROM ontime.flight f;
