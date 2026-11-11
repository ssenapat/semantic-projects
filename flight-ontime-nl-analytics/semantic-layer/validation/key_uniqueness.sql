-- Key uniqueness checks (REQ-020). Read-only: every block is a single SELECT.
-- Block format: CONTRACT C-4.1. Expectations: CONTRACT C-4.2.

-- @check: flight_pk
-- @description: ontime.flight primary key has no duplicate and no NULL values
-- @expect: {"duplicate_keys": 0, "null_keys": 0}
SELECT COUNT(*) - COUNT(DISTINCT f.flight_id) AS duplicate_keys,
       COUNT(*) FILTER (WHERE f.flight_id IS NULL) AS null_keys
FROM ontime.flight f;

-- @check: carrier_keys_unique
-- @description: carrier id and carrier code are unique (no unique constraint is declared on unique_carrier)
-- @expect: {"duplicate_airline_ids": 0, "duplicate_codes": 0}
SELECT COUNT(*) - COUNT(DISTINCT c.airline_id) AS duplicate_airline_ids,
       COUNT(*) - COUNT(DISTINCT c.unique_carrier) AS duplicate_codes
FROM ontime.carrier c;

-- @check: airport_keys_unique
-- @description: airport id and airport sequence id are unique
-- @expect: {"duplicate_airport_ids": 0, "duplicate_seq_ids": 0}
SELECT COUNT(*) - COUNT(DISTINCT a.airport_id) AS duplicate_airport_ids,
       COUNT(*) - COUNT(DISTINCT a.airport_seq_id) AS duplicate_seq_ids
FROM ontime.airport a;

-- @check: carrier_code_consistency
-- @description: flight.op_unique_carrier equals carrier.unique_carrier for every flight
-- @expect: {"op_unique_carrier_mismatches": 0}
SELECT COUNT(*) FILTER (WHERE f.op_unique_carrier <> c.unique_carrier) AS op_unique_carrier_mismatches
FROM ontime.flight f
JOIN ontime.carrier c ON c.airline_id = f.airline_id;

-- @check: destination_only_airports
-- @description: airports without a code appear only as destinations (known caveat, one airport)
-- @expect: {"null_code_airports": 1}
-- @severity: warn
SELECT COUNT(*) FILTER (WHERE a.code IS NULL) AS null_code_airports
FROM ontime.airport a;
