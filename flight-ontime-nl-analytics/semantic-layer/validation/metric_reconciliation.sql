-- Metric reconciliation (REQ-021). Read-only: every block is a single SELECT or WITH.
-- Block format: CONTRACT C-4.1. Expectations: CONTRACT C-4.2. Spec 14.2 and 14.3.
-- Counts are exact, rates must be within 0.0001 of the baseline (as of 2026-10-12).
-- Rates are 0 to 1 ratios, the views report percentages with 2 decimals.

-- @check: baseline_totals
-- @description: baseline counts and rates computed independently from ontime.flight
-- @expect: {"flights": 544003, "operated_flights": 518368, "cancelled": 25635, "diverted": 1146, "dep_delayed": 107297, "dep_flagged": 518774, "arr_delayed": 107475, "arr_flagged": 517222, "cancellation_rate": 0.0471, "departure_delay_rate": 0.2068, "arrival_delay_rate": 0.2078, "on_time_arrival_rate": 0.7922, "diversion_rate": 0.0022}
-- @tolerance: 0.0001
SELECT COUNT(*) AS flights,
       COUNT(*) FILTER (WHERE f.cancelled = 0) AS operated_flights,
       SUM(f.cancelled) AS cancelled,
       SUM(f.diverted) AS diverted,
       SUM(f.dep_del15) AS dep_delayed,
       COUNT(f.dep_del15) AS dep_flagged,
       SUM(f.arr_del15) AS arr_delayed,
       COUNT(f.arr_del15) AS arr_flagged,
       ROUND(SUM(f.cancelled)::numeric / COUNT(*), 4) AS cancellation_rate,
       ROUND(AVG(f.dep_del15)::numeric, 4) AS departure_delay_rate,
       ROUND(AVG(f.arr_del15)::numeric, 4) AS arrival_delay_rate,
       ROUND(COUNT(*) FILTER (WHERE f.arr_del15 = 0)::numeric / NULLIF(COUNT(f.arr_del15), 0), 4) AS on_time_arrival_rate,
       ROUND(SUM(f.diverted)::numeric / NULLIF(COUNT(*) FILTER (WHERE f.cancelled = 0), 0), 4) AS diversion_rate
FROM ontime.flight f;

-- @check: carrier_month_vs_view
-- @description: carrier-month flights and arrival delay percentage versus ontime.v_carrier_monthly_performance (spec 14.2, no rows expected)
-- @expect_rows: 0
SELECT s.unique_carrier,
       s.month,
       s.flights,
       s.flights - v.flights AS flights_diff,
       s.pct_arr - v.pct_arr_delayed_15 AS arr_pct_diff
FROM (
  SELECT c.unique_carrier,
         DATE_TRUNC('month', f.fl_date)::date AS month,
         COUNT(*) AS flights,
         ROUND(100.0 * AVG(f.arr_del15), 2) AS pct_arr
  FROM ontime.flight f
  JOIN ontime.carrier c ON c.airline_id = f.airline_id
  GROUP BY c.unique_carrier, DATE_TRUNC('month', f.fl_date)::date
) s
JOIN ontime.v_carrier_monthly_performance v
  ON v.unique_carrier = s.unique_carrier AND v.month = s.month
WHERE s.flights <> v.flights OR s.pct_arr <> v.pct_arr_delayed_15
ORDER BY s.unique_carrier, s.month;

-- @check: carrier_view_row_count
-- @description: rows in ontime.v_carrier_monthly_performance minus distinct (carrier, month) groups of the fact
-- @expect: {"row_difference": 0}
SELECT (SELECT COUNT(*) FROM ontime.v_carrier_monthly_performance v)
       - (SELECT COUNT(*)
          FROM (SELECT DISTINCT c.unique_carrier, DATE_TRUNC('month', f.fl_date)::date AS month
                FROM ontime.flight f
                JOIN ontime.carrier c ON c.airline_id = f.airline_id) g) AS row_difference;

-- @check: route_pair_count
-- @description: distinct (origin airport id, destination airport id) pairs, compared on ids not codes (spec 14.3)
-- @expect: {"route_pairs": 5812}
SELECT COUNT(*) AS route_pairs
FROM (SELECT DISTINCT f.origin_airport_id, f.dest_airport_id FROM ontime.flight f) r;

-- @check: route_view_flight_total
-- @description: total flights in ontime.v_route_summary minus fact row count
-- @expect: {"flight_difference": 0}
SELECT (SELECT SUM(v.flights) FROM ontime.v_route_summary v)
       - (SELECT COUNT(*) FROM ontime.flight f) AS flight_difference;
