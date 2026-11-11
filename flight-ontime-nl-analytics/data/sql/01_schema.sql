\set ON_ERROR_STOP on
CREATE SCHEMA IF NOT EXISTS ontime;
SET search_path = ontime;

-- Raw landing table: every CSV column as text, same order as the BTS 109-column download header
-- (the file's header and rows end with a trailing comma, hence the last column)
DROP TABLE IF EXISTS stg_flight;
CREATE TABLE stg_flight (
 year text, quarter text, month text, dayofmonth text, dayofweek text, flightdate text,
 reporting_airline text, dot_id_reporting_airline text, iata_code_reporting_airline text, tail_number text, flight_number_reporting_airline text, originairportid text,
 originairportseqid text, origincitymarketid text, origin text, origincityname text, originstate text, originstatefips text,
 originstatename text, originwac text, destairportid text, destairportseqid text, destcitymarketid text, dest text,
 destcityname text, deststate text, deststatefips text, deststatename text, destwac text, crsdeptime text,
 deptime text, depdelay text, depdelayminutes text, depdel15 text, departuredelaygroups text, deptimeblk text,
 taxiout text, wheelsoff text, wheelson text, taxiin text, crsarrtime text, arrtime text,
 arrdelay text, arrdelayminutes text, arrdel15 text, arrivaldelaygroups text, arrtimeblk text, cancelled text,
 cancellationcode text, diverted text, crselapsedtime text, actualelapsedtime text, airtime text, flights text,
 distance text, distancegroup text, carrierdelay text, weatherdelay text, nasdelay text, securitydelay text,
 lateaircraftdelay text, firstdeptime text, totaladdgtime text, longestaddgtime text, divairportlandings text, divreacheddest text,
 divactualelapsedtime text, divarrdelay text, divdistance text, div1airport text, div1airportid text, div1airportseqid text,
 div1wheelson text, div1totalgtime text, div1longestgtime text, div1wheelsoff text, div1tailnum text, div2airport text,
 div2airportid text, div2airportseqid text, div2wheelson text, div2totalgtime text, div2longestgtime text, div2wheelsoff text,
 div2tailnum text, div3airport text, div3airportid text, div3airportseqid text, div3wheelson text, div3totalgtime text,
 div3longestgtime text, div3wheelsoff text, div3tailnum text, div4airport text, div4airportid text, div4airportseqid text,
 div4wheelson text, div4totalgtime text, div4longestgtime text, div4wheelsoff text, div4tailnum text, div5airport text,
 div5airportid text, div5airportseqid text, div5wheelson text, div5totalgtime text, div5longestgtime text, div5wheelsoff text,
 div5tailnum text, trailing_empty text
);

CREATE TABLE IF NOT EXISTS carrier (
 airline_id     integer  PRIMARY KEY,
 unique_carrier varchar(2) NOT NULL
);

CREATE TABLE IF NOT EXISTS airport (
 airport_id     integer  PRIMARY KEY,
 airport_seq_id integer  NOT NULL,
 city_market_id integer  NOT NULL,
 code           char(3)  NOT NULL,
 city_name      varchar(60) NOT NULL,
 state_abr      char(2)  NOT NULL,
 wac            smallint NOT NULL
);

CREATE TABLE IF NOT EXISTS flight (
 flight_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 fl_date           date     NOT NULL,
 quarter           smallint NOT NULL CHECK (quarter BETWEEN 1 AND 4),
 month             smallint NOT NULL CHECK (month BETWEEN 1 AND 12),
 day_of_week       smallint NOT NULL CHECK (day_of_week BETWEEN 1 AND 7),
 airline_id        integer  NOT NULL REFERENCES carrier(airline_id),
 op_unique_carrier varchar(2) NOT NULL,
 op_carrier        varchar(2) NOT NULL,
 origin_airport_id integer  NOT NULL REFERENCES airport(airport_id),
 dest_airport_id   integer  NOT NULL REFERENCES airport(airport_id),
 dep_del15         smallint CHECK (dep_del15 IN (0,1)),
 dep_delay_group   smallint,
 dep_time_blk      char(9),
 arr_del15         smallint CHECK (arr_del15 IN (0,1)),
 arr_delay_group   smallint,
 arr_time_blk      char(9),
 cancelled         smallint NOT NULL CHECK (cancelled IN (0,1)),
 cancellation_code char(1),
 diverted          smallint NOT NULL CHECK (diverted IN (0,1)),
 distance_group    smallint,
 div_airport_landings smallint,
 div_reached_dest  smallint,
 div1_airport_id integer, div1_airport_seq_id integer,
 div2_airport_id integer, div2_airport_seq_id integer,
 div3_airport_id integer, div3_airport_seq_id integer,
 div4_airport_id integer, div4_airport_seq_id integer,
 div5_airport_id integer, div5_airport_seq_id integer
);
