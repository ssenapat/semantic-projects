# flight-ontime-nl-analytics

Natural-language analytics over BTS on-time flight data. A question goes through four local pieces:

| Piece | Folder | Default address | Role |
|---|---|---|---|
| Parser | `llm-intent-parser-service` | `http://127.0.0.1:8081` | question + safe context -> one schema-valid semantic request (Claude on AWS Bedrock, or an offline stub) |
| Query | `semantic-query-service` | `http://127.0.0.1:8082` | validates the request, compiles guarded SELECT SQL, runs it read-only on PostgreSQL |
| Retriever | `semantic-retrieval-service` | `http://127.0.0.1:8080` | public API (`/api/v1/ask`): retrieves context, calls Parser then Query |
| UI | `streamlit-analytics-client` | `http://127.0.0.1:8501` | Streamlit front end that calls the Retriever |

Also in this folder: `semantic-layer/` (metric/model definitions, compiler, validator), `e2e/` (cross-service and contract tests), `data/` (see `data/README.md` for loading the CSV into Postgres), `NL_ANALYTICS_README.md` (architecture and env-var reference).

## Data source

The data is the **US Bureau of Transportation Statistics (BTS) "Reporting Carrier On-Time Performance (1987-present)"** table, downloaded from BTS TranStats:

<https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FGJ&QO_fu146_anzr=b0-gvzr>

**Download steps**
1. Open the link above.
2. Tick **every** field on the page ("select all"). This gives 109 columns.
3. Set **Filter Year** to the year and **Filter Period** to the month you want. One year and one period per download.
4. Click Download. BTS returns a zip containing the CSV and a `readme.html` (field descriptions). The zip, folder and CSV names end in `_<year>_<period>`; the BTS readme's example `XXX_XXXXX_2001_1` is the first month of 2001.

**Copy currently on disk** (downloaded 2026-10-31, kept in `data/scripts/20261031/`):
- Folder `On_Time_Reporting_Carrier_On_Time_Performance_1987_present_2026_1/`. The `2026_1` suffix means year 2026, period 1 (January).
- File `On_Time_Reporting_Carrier_On_Time_Performance_(1987_present)_2026_1.csv`, about 245 MB, 544,003 data rows, 109 columns, all dated 2026-01-01 to 2026-01-31. It is the same flights as the database but with all columns.
- Columns: `Year`, `Quarter`, `Month`, `DayofMonth`, `DayOfWeek`, `FlightDate`; airline (`Reporting_Airline`, `DOT_ID_Reporting_Airline`, `IATA_CODE_Reporting_Airline`, `Tail_Number`, `Flight_Number_Reporting_Airline`); origin and destination (airport ID, sequence ID, city market ID, code, city name, state, state FIPS, state name, WAC); departure and arrival times, delays, delay groups and time blocks; taxi and wheels times; `Cancelled`, `CancellationCode`, `Diverted`; elapsed and air times; `Flights`, `Distance`, `DistanceGroup`; delay causes (`CarrierDelay`, `WeatherDelay`, `NASDelay`, `SecurityDelay`, `LateAircraftDelay`); and the diversion fields `Div1` to `Div5` (airport, ID, sequence ID, wheels on/off, times, tail number). Meanings are summarised below and in the zip's `readme.html`.

**Field reference** (from the BTS `readme.html` shipped with the download; fields appear in the CSV in this order)

| Group | Fields | Meaning |
|---|---|---|
| Time | `Year`, `Quarter` (1-4), `Month`, `DayofMonth`, `DayOfWeek`, `FlightDate` (yyyymmdd) | Flight date parts |
| Airline | `Reporting_Airline` | Unique carrier code. Use it to analyse carriers across years. A numeric suffix (for example `PA(1)`) marks a code reused by a different carrier earlier, so it is not always unique |
| | `DOT_ID_Reporting_Airline` | Carrier ID assigned by US DOT; one carrier keeps one ID even if its code changes. Use for analysis across years |
| | `IATA_CODE_Reporting_Airline` | IATA code. It can be reused by different carriers over time |
| | `Tail_Number`, `Flight_Number_Reporting_Airline` | Aircraft tail and flight number |
| Origin | `OriginAirportID`, `OriginAirportSeqID`, `OriginCityMarketID`, `Origin`, `OriginCityName`, `OriginState`, `OriginStateFips`, `OriginStateName`, `OriginWac` | `OriginAirportID` is the DOT airport key. The airport code can change over time and be reused, so use `OriginAirportSeqID` (the airport at a point in time, since its attributes such as name and coordinates can change) for time-specific facts. `OriginCityMarketID` groups the airports serving one city market. `OriginWac` is the World Area Code |
| Destination | `DestAirportID`, `DestAirportSeqID`, `DestCityMarketID`, `Dest`, `DestCityName`, `DestState`, `DestStateFips`, `DestStateName`, `DestWac` | Same fields for the destination |
| Departure | `CRSDepTime`, `DepTime` (local hhmm), `DepDelay`, `DepDelayMinutes`, `DepDel15`, `DepartureDelayGroups`, `DepTimeBlk` | Scheduled vs actual departure. Delays are in minutes and negative when early (`DepDelayMinutes` is 0 when early). `DepDel15` = 1 if 15 minutes or more late. Delay groups are 15-minute intervals. `DepTimeBlk` is the hourly block |
| Taxi and wheels | `TaxiOut`, `WheelsOff`, `WheelsOn`, `TaxiIn` | Taxi times in minutes; wheels-off and wheels-on in local hhmm |
| Arrival | `CRSArrTime`, `ArrTime`, `ArrDelay`, `ArrDelayMinutes`, `ArrDel15`, `ArrivalDelayGroups`, `ArrTimeBlk` | Same as departure, for arrival |
| Status | `Cancelled` (1 = yes), `CancellationCode`, `Diverted` (1 = yes) | `CancellationCode` gives the reason for cancellation |
| Duration and distance | `CRSElapsedTime`, `ActualElapsedTime`, `AirTime`, `Flights`, `Distance` (miles), `DistanceGroup` | Elapsed and air time in minutes; `Flights` is the number of flights (1 per row); `DistanceGroup` is in 250-mile intervals |
| Delay causes | `CarrierDelay`, `WeatherDelay`, `NASDelay` (National Air System), `SecurityDelay`, `LateAircraftDelay` | Minutes of delay by cause |
| Diversion summary | `FirstDepTime`, `TotalAddGTime`, `LongestAddGTime`, `DivAirportLandings`, `DivReachedDest`, `DivActualElapsedTime`, `DivArrDelay`, `DivDistance` | First gate departure time, additional ground time away from the gate (total and longest) for cancelled or diverted flights, number of diverted landings, and whether the flight reached its destination. `ArrDelay` stays NULL for all diverted flights; `DivArrDelay` is the difference between scheduled and final-diverted-airport arrival; `DivDistance` is the distance from the destination to the final diverted airport (0 if the flight did not divert) |
| Diverted airports 1-5 | `Div1Airport`, `Div1AirportID`, `Div1AirportSeqID`, `Div1WheelsOn`, `Div1TotalGTime`, `Div1LongestGTime`, `Div1WheelsOff`, `Div1TailNum`, and the same eight fields for `Div2` to `Div5` | Details for each diversion stop (40 columns) |

**What is loaded in Postgres today.** The database was reloaded from the seven monthly 109-column files January to July 2026 (`data/Jan2026.csv` ... `data/Jul2026.csv`): 4,120,343 flights (2026-01-01 to 2026-07-31), 13 carriers, 357 airports. The loader (`data/scripts/load.sh`, with `data/sql/01_schema.sql` and `02_transform.sql`) reads all 109 columns into a staging table, but the `ontime.flight` table keeps only the curated columns the services use (delay minutes, times and distance are not carried over). The loader is safe to rerun: it creates missing tables and empties `flight`, `airport` and `carrier` before loading.

**More months.** Download each month the same way (Year 2026, one Period per file). BTS publishes with a lag, so the latest months may not be available yet.

The CSV and zip files are large and are not meant to be committed to git. Load with `data/scripts/load.sh [file-or-dir ...]` (no argument loads every `*.csv` in `data/`) (details in `data/README.md`).

Requirements: Python 3.11+, PostgreSQL with the `tran-stats` database loaded (only for real queries and `db` tests), and for the real LLM an AWS account with Amazon Bedrock access to a Claude model.

## Run everything (recommended)

```bash
./run-local.sh setup               # creates .venv and installs all services
./run-local.sh start --stub-llm    # offline, deterministic Parser, no LLM and no AWS needed
# or, with the real LLM on AWS Bedrock (see "LLM on AWS Bedrock" below):
export AWS_REGION=us-east-1 BEDROCK_MODEL_ID=<your-bedrock-model-or-inference-profile-id>
./run-local.sh start
./run-local.sh status              # query / parser / retriever / ui: ready|stopped
./run-local.sh stop
```

- Without `--stub-llm`, the Parser calls Claude on **AWS Bedrock** (the default `PARSER_LLM_CLIENT=bedrock`). No API key is needed: it uses your AWS credentials (`AWS_PROFILE`, SSO, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or an instance role). Set `AWS_REGION` and `BEDROCK_MODEL_ID` (a Bedrock model or inference-profile id enabled for your account), for example `export AWS_REGION=us-east-1 BEDROCK_MODEL_ID=<your-model-id>`. The direct Anthropic API is still available with `PARSER_LLM_CLIENT=anthropic` and `ANTHROPIC_API_KEY`.
- Query needs `PGPASSWORD` for PostgreSQL (see "Secrets" below).
- Ports can be changed with `QUERY_PORT`, `PARSER_PORT`, `RETRIEVER_PORT`, `UI_PORT`. Start waits up to `READY_WAIT_SECONDS` (60) for each service.
- PIDs and logs are kept in `.run/`.

## LLM on AWS Bedrock

The Parser is the only piece that calls an LLM. By default it uses Claude through Amazon Bedrock, so there is no LLM API key to create or store.

| Setting | Meaning |
|---|---|
| AWS credentials | Standard AWS chain: `AWS_PROFILE`, SSO login, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`, or an instance role. The identity needs `bedrock:InvokeModel`. |
| `AWS_REGION` | Region where Bedrock model access is enabled (`AWS_DEFAULT_REGION` also works). |
| `BEDROCK_MODEL_ID` | Bedrock model or inference-profile id for a Claude model enabled in your account. |
| `PARSER_LLM_CLIENT` | `bedrock` (default), `stub` (offline, no LLM), or `anthropic` (direct Anthropic API, needs `ANTHROPIC_API_KEY`). |

Setup: enable model access for Claude in the Bedrock console, run `aws sso login` (or set your profile), export `AWS_REGION` and `BEDROCK_MODEL_ID`, then `./run-local.sh start`. `run-local.sh` stops with a clear message if either variable is missing, or if `BEDROCK_MODEL_ID` has no inference-profile prefix (`us.`, `eu.`, `apac.`, `global.`, or a full ARN), because Bedrock rejects a bare model id with a 502 `llm_provider_failure`. For example use `us.anthropic.claude-sonnet-5-5`, not `anthropic.claude-sonnet-5-5`. The `boto3`/`anthropic[bedrock]` dependency is installed by `./run-local.sh setup`. Unit and E2E tests never call Bedrock; they use the stub.

## Run each service by hand

Activate the shared venv first (`source .venv/bin/activate`) or use `.venv/bin/...`. All commands run from the service's own folder unless noted.

Each service that reads the catalog needs `SEMANTIC_LAYER_SRC` and `SEMANTIC_LAYER_DEFINITIONS_DIR` (absolute paths to `semantic-layer/src` and `semantic-layer/definitions`). `run-local.sh` sets them for you.

**Parser** (`llm-intent-parser-service`)
```bash
pip install -e ".[test]"
PARSER_LLM_CLIENT=stub uvicorn llm_intent_parser_service.app.main:app --host 127.0.0.1 --port 8081
# real LLM on AWS Bedrock (needs AWS credentials in the environment):
AWS_REGION=... BEDROCK_MODEL_ID=... uvicorn llm_intent_parser_service.app.main:app --host 127.0.0.1 --port 8081
```

**Query** (`semantic-query-service`), needs PostgreSQL
```bash
pip install -e ".[dev]"
uvicorn semantic_query_service.app.main:app --host 127.0.0.1 --port 8082
```

**Retriever** (`semantic-retrieval-service`), needs Parser and Query running
```bash
pip install -e ".[dev]"
uvicorn semantic_retrieval_service.app.main:app --host 127.0.0.1 --port 8080
```

**UI** (`streamlit-analytics-client`)
```bash
pip install -r requirements.txt
streamlit run streamlit_analytics_client/app.py --server.address 127.0.0.1 --server.port 8501
```

Health checks: the services expose `/health/live` and `/health/ready`; the UI uses `/_stcore/health`. OpenAPI docs are at `/openapi.json`.

## Tests

Unit tests are offline (stub LLM, mocked HTTP, no database). Run from each folder with the shared venv:

| Service | Command (from the service folder) | Last result |
|---|---|---|
| llm-intent-parser-service | `../.venv/bin/python -m pytest -q` | 88 passed |
| semantic-query-service | `../.venv/bin/python -m pytest -q` | 85 passed |
| semantic-retrieval-service | `../.venv/bin/python -m pytest -q` | 48 passed, 1 skipped |
| streamlit-analytics-client | `../.venv/bin/python -m pytest -q` | 28 passed |
| semantic-layer | `PYTHONPATH=src ../.venv/bin/python -m pytest tests -q` | 219 passed; `test_integration_db.py` needs Postgres |

End-to-end and contract checks, run from this folder:

```bash
.venv/bin/python e2e/contract_check.py --static          # schema parity + OpenAPI paths, no servers
.venv/bin/python e2e/contract_check.py --live            # with services running: routes + error envelope
.venv/bin/python -m pytest e2e -m "not db" -q            # E2E with stub LLM, no database
PGPASSWORD=... .venv/bin/python -m pytest e2e -m db -q   # real six-question flow (SELECT only)
```

`e2e/test_no_secrets.py` fails if credentials appear in tracked files.

## Secrets

Credentials come from the environment only: `PGPASSWORD`, plus AWS credentials from the standard AWS chain for Bedrock. There is no LLM API key, unless you choose `PARSER_LLM_CLIENT=anthropic`, which needs `ANTHROPIC_API_KEY`. Never commit values and never print them.
