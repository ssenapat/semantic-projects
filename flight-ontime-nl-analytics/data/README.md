# semantic-projects: tran-stats

PostgreSQL database built from the BTS On-Time Reporting CSV (109-column download, Jan 2026, 544,003 rows).

## Local Postgres
```bash
docker run --name pg-local -e POSTGRES_PASSWORD=<redacted - set via PGPASSWORD env> \
  -p 5432:5432 -v pgdata:/var/lib/postgresql -d postgres   # PG 18+: mount /var/lib/postgresql
```
DBeaver: host `localhost`, port `5432`, db `tran-stats`, user `postgres`, password `<redacted - set via PGPASSWORD env>` (local dev only).

## Rebuild
```bash
./scripts/load.sh                 # loads every *.csv in this data folder (Jan2026.csv ... Jul2026.csv)
./scripts/load.sh path/to/dir-or-files.csv   # or a directory / specific files
# the load empties flight/airport/carrier first, so rerunning never duplicates rows
# to start over: docker exec pg-local psql -U postgres -c 'DROP DATABASE "tran-stats"'
```
The CSV is not stored in this folder (about 100 MB).

## Layout
- `sql/01_schema.sql`: schema `ontime`, staging table, `carrier`, `airport`, `flight`
- `sql/02_transform.sql`: fills dimensions and `flight`, adds indexes and views, drops staging
- `scripts/load.sh`: runs the whole pipeline against the Docker container

## Objects (schema `ontime`)
`flight` (fact), `airport` (342), `carrier` (13), views `v_carrier_monthly_performance` and `v_route_summary`.

## Notes
- Only origin rows carry the airport code and WAC, so destination-only airports have NULL `code`/`wac`.
- The database name has a hyphen, so quote it in SQL: `"tran-stats"`.
