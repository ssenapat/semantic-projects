#!/usr/bin/env bash
# Loads the BTS On-Time CSVs (the 109-column "select all fields" download, one file per month)
# into the "tran-stats" database.
#
# Usage: load.sh [FILE_OR_DIR ...]
#   No arguments: loads every *.csv in the data folder (the folder above scripts/), e.g. Jan2026.csv ... Jul2026.csv.
#   A directory argument loads every *.csv in it; file arguments are loaded in the order given.
#
# Safe to rerun: creates the database/tables if missing, EMPTIES flight/airport/carrier first, then reloads
# everything, so rerunning never duplicates rows. Each CSV header is checked against the staging table
# before anything is deleted, so a changed or extra column stops the load with a clear message.
set -euo pipefail
CONTAINER="${PG_CONTAINER:-pg-local}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PSQL=(docker exec -i "$CONTAINER" psql -U postgres -v ON_ERROR_STOP=1)

# Collect the CSV files
FILES=()
if [ "$#" -eq 0 ]; then set -- "$HERE"; fi
for arg in "$@"; do
  if [ -d "$arg" ]; then
    while IFS= read -r f; do FILES+=("$f"); done < <(find "$arg" -maxdepth 1 -type f -name '*.csv' | sort)
  elif [ -f "$arg" ]; then
    FILES+=("$arg")
  else
    echo "CSV not found: $arg" >&2; exit 1
  fi
done
[ "${#FILES[@]}" -gt 0 ] || { echo "No CSV files found" >&2; exit 1; }

if ! "${PSQL[@]}" -d postgres -Atc "select 1 from pg_database where datname='tran-stats'" | grep -q 1; then
  "${PSQL[@]}" -d postgres -c 'CREATE DATABASE "tran-stats"'
fi
"${PSQL[@]}" -d tran-stats < "$HERE/sql/01_schema.sql"

# Staging columns, in order (the last one, trailing_empty, absorbs the trailing comma on each row)
EXPECTED="$("${PSQL[@]}" -d tran-stats -Atc "select column_name from information_schema.columns where table_schema='ontime' and table_name='stg_flight' order by ordinal_position" | tr '\n' ',' )"
for f in "${FILES[@]}"; do
  HEADER="$(head -1 "$f" | tr -d '\r"' | tr 'A-Z' 'a-z')"
  # The header ends with a trailing comma, which maps to the trailing_empty staging column
  if [ "${HEADER}trailing_empty," != "${EXPECTED}" ]; then
    echo "ERROR: header of $f does not match ontime.stg_flight; update sql/01_schema.sql and sql/02_transform.sql first" >&2
    diff <(echo "$HEADER" | tr ',' '\n') <(echo "$EXPECTED" | tr ',' '\n') >&2 || true
    exit 1
  fi
done

"${PSQL[@]}" -d tran-stats -c "TRUNCATE ontime.flight, ontime.airport, ontime.carrier RESTART IDENTITY"
for f in "${FILES[@]}"; do
  echo "loading $f"
  "${PSQL[@]}" -d tran-stats -c "\copy ontime.stg_flight FROM STDIN WITH (FORMAT csv, HEADER true)" < "$f"
done
"${PSQL[@]}" -d tran-stats < "$HERE/sql/02_transform.sql"
"${PSQL[@]}" -d tran-stats -c "select count(*) as flights, min(fl_date), max(fl_date) from ontime.flight"
