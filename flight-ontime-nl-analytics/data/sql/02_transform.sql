\set ON_ERROR_STOP on
SET search_path = ontime;
BEGIN;
ALTER TABLE airport ALTER COLUMN code DROP NOT NULL, ALTER COLUMN wac DROP NOT NULL;

INSERT INTO carrier (airline_id, unique_carrier)
SELECT DISTINCT ON (dot_id_reporting_airline::int) dot_id_reporting_airline::int, reporting_airline
FROM stg_flight ORDER BY dot_id_reporting_airline::int, reporting_airline;

-- Prefer origin-side rows (they carry code + WAC); fall back to destination-only airports
INSERT INTO airport (airport_id, airport_seq_id, city_market_id, code, city_name, state_abr, wac)
SELECT DISTINCT ON (airport_id) airport_id, seq_id, mkt, code, city, st, wac FROM (
  SELECT DISTINCT originairportid::int airport_id, originairportseqid::int seq_id, origincitymarketid::int mkt,
         origin::char(3) code, origincityname city, originstate st, originwac::smallint wac FROM stg_flight
  UNION ALL
  SELECT DISTINCT destairportid::int, destairportseqid::int, destcitymarketid::int,
         NULL::char(3), destcityname, deststate, NULL::smallint FROM stg_flight
) u ORDER BY airport_id, (code IS NULL), seq_id DESC;

INSERT INTO flight (fl_date, quarter, month, day_of_week, airline_id, op_unique_carrier, op_carrier,
  origin_airport_id, dest_airport_id, dep_del15, dep_delay_group, dep_time_blk, arr_del15, arr_delay_group, arr_time_blk,
  cancelled, cancellation_code, diverted, distance_group, div_airport_landings, div_reached_dest,
  div1_airport_id, div1_airport_seq_id, div2_airport_id, div2_airport_seq_id, div3_airport_id, div3_airport_seq_id,
  div4_airport_id, div4_airport_seq_id, div5_airport_id, div5_airport_seq_id)
SELECT CASE WHEN flightdate LIKE '%/%' THEN to_date(split_part(flightdate,' ',1),'MM/DD/YYYY') ELSE flightdate::date END, quarter::smallint, month::smallint, dayofweek::smallint,
  dot_id_reporting_airline::int, reporting_airline, iata_code_reporting_airline, originairportid::int, destairportid::int,
  NULLIF(depdel15,'')::numeric::smallint, NULLIF(departuredelaygroups,'')::smallint, deptimeblk,
  NULLIF(arrdel15,'')::numeric::smallint, NULLIF(arrivaldelaygroups,'')::smallint, arrtimeblk,
  cancelled::numeric::smallint, NULLIF(cancellationcode,''), diverted::numeric::smallint, distancegroup::smallint,
  NULLIF(divairportlandings,'')::numeric::smallint, NULLIF(divreacheddest,'')::numeric::smallint,
  NULLIF(div1airportid,'')::int, NULLIF(div1airportseqid,'')::int, NULLIF(div2airportid,'')::int, NULLIF(div2airportseqid,'')::int,
  NULLIF(div3airportid,'')::int, NULLIF(div3airportseqid,'')::int, NULLIF(div4airportid,'')::int, NULLIF(div4airportseqid,'')::int,
  NULLIF(div5airportid,'')::int, NULLIF(div5airportseqid,'')::int
FROM stg_flight;

CREATE INDEX IF NOT EXISTS ix_flight_fl_date ON flight (fl_date);
CREATE INDEX IF NOT EXISTS ix_flight_origin  ON flight (origin_airport_id);
CREATE INDEX IF NOT EXISTS ix_flight_dest    ON flight (dest_airport_id);
CREATE INDEX IF NOT EXISTS ix_flight_carrier ON flight (airline_id, fl_date);

CREATE OR REPLACE VIEW v_carrier_monthly_performance AS
SELECT c.unique_carrier, date_trunc('month', f.fl_date)::date AS month,
       count(*) AS flights, sum(f.cancelled) AS cancelled, sum(f.diverted) AS diverted,
       round(100.0*avg(f.dep_del15),2) AS pct_dep_delayed_15, round(100.0*avg(f.arr_del15),2) AS pct_arr_delayed_15
FROM flight f JOIN carrier c USING (airline_id) GROUP BY 1,2;

CREATE OR REPLACE VIEW v_route_summary AS
SELECT o.code AS origin, o.city_name AS origin_city, d.city_name AS dest_city, f.dest_airport_id,
       count(*) AS flights, round(100.0*avg(f.arr_del15),2) AS pct_arr_delayed_15
FROM flight f JOIN airport o ON o.airport_id=f.origin_airport_id JOIN airport d ON d.airport_id=f.dest_airport_id
GROUP BY 1,2,3,4;

COMMIT;
DROP TABLE stg_flight;
ANALYZE;
