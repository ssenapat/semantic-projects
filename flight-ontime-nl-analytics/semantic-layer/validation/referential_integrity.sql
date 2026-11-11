-- Referential integrity checks (REQ-014, REQ-020). Read-only: every block is a single SELECT.
-- Block format: CONTRACT C-4.1. Expectations: CONTRACT C-4.2.

-- @check: orphans
-- @description: flights whose carrier or airports are missing from the dimensions (LEFT JOIN form, spec 14.1)
-- @expect: {"orphan_carriers": 0, "orphan_airports": 0}
SELECT COUNT(*) FILTER (WHERE c.airline_id IS NULL) AS orphan_carriers,
       COUNT(*) FILTER (WHERE ao.airport_id IS NULL OR ad.airport_id IS NULL) AS orphan_airports
FROM ontime.flight f
LEFT JOIN ontime.carrier c ON c.airline_id = f.airline_id
LEFT JOIN ontime.airport ao ON ao.airport_id = f.origin_airport_id
LEFT JOIN ontime.airport ad ON ad.airport_id = f.dest_airport_id;

-- @check: join_row_preservation
-- @description: fact rows minus rows after the three inner joins (carrier, origin airport, destination airport)
-- @expect: {"row_difference": 0}
SELECT (SELECT COUNT(*) FROM ontime.flight f0)
       - (SELECT COUNT(*)
          FROM ontime.flight f
          JOIN ontime.carrier c ON c.airline_id = f.airline_id
          JOIN ontime.airport ao ON ao.airport_id = f.origin_airport_id
          JOIN ontime.airport ad ON ad.airport_id = f.dest_airport_id) AS row_difference;
