#!/usr/bin/env bash
# Local runner for the pagila-semantic stack. All services bind 127.0.0.1.
#
# Usage: run-local.sh {start|stop|restart|status} [--no-db]
#
#   start    start PostgreSQL container (pg-local), query service, intent parser, UI
#   stop     stop the three services and the UI (the DB container is left running
#            unless you pass --stop-db)
#   status   show state of each piece
#
# Secrets: PAGILA_PG_USER / PAGILA_PG_PASSWORD are read from ./.env (never printed).
# The intent parser calls Claude on AWS Bedrock with your AWS credentials, so run
# `aws sso login` first (or have AWS_PROFILE / keys in the environment).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
RUN="$ROOT/.run"
DB_CONTAINER="${DB_CONTAINER:-pg-local}"

QUERY_PORT="${QUERY_PORT:-8000}"
PARSER_PORT="${PARSER_PORT:-8001}"
UI_PORT="${UI_PORT:-8511}"
READY_WAIT_SECONDS="${READY_WAIT_SECONDS:-60}"

export AWS_REGION="${AWS_REGION:-ap-south-1}"
export INTENT_MODEL="${INTENT_MODEL:-global.anthropic.claude-sonnet-5-5}"

SERVICES=(query parser ui)

usage() { echo "usage: $(basename "$0") {start|stop|restart|status} [--no-db|--stop-db]" >&2; exit 2; }

load_env() {
  if [ -f "$ROOT/.env" ]; then
    set -a; . "$ROOT/.env"; set +a
  fi
  if [ -z "${PAGILA_PG_USER:-}" ] || [ -z "${PAGILA_PG_PASSWORD:-}" ]; then
    echo "ERROR: PAGILA_PG_USER and PAGILA_PG_PASSWORD must be set (in $ROOT/.env)" >&2
    exit 1
  fi
}

url_for() {
  case "$1" in
    query)  echo "http://127.0.0.1:$QUERY_PORT/ready" ;;
    parser) echo "http://127.0.0.1:$PARSER_PORT/health" ;;
    ui)     echo "http://127.0.0.1:$UI_PORT/_stcore/health" ;;
  esac
}

is_ready() { curl -fsS --max-time 3 "$(url_for "$1")" >/dev/null 2>&1; }

pid_alive() {
  local f="$RUN/$1.pid"
  [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null
}

port_for() {
  case "$1" in query) echo "$QUERY_PORT" ;; parser) echo "$PARSER_PORT" ;; ui) echo "$UI_PORT" ;; esac
}

check_venv() {
  if [ ! -x "$VENV/bin/python" ]; then
    echo "ERROR: $VENV not found. Create it first (see README section 2)." >&2
    exit 1
  fi
}

start_db() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "WARN: docker not installed; assuming PostgreSQL is reachable on localhost:5432" >&2
    return 0
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is not running. Start Docker Desktop, then retry (or pass --no-db)." >&2
    exit 1
  fi
  local state
  state="$(docker inspect -f '{{.State.Running}}' "$DB_CONTAINER" 2>/dev/null || echo missing)"
  case "$state" in
    true)    echo "db: $DB_CONTAINER already running" ;;
    false)   echo "db: starting $DB_CONTAINER"; docker start "$DB_CONTAINER" >/dev/null || exit 1 ;;
    missing) echo "ERROR: container '$DB_CONTAINER' does not exist. See README section 1 to create it." >&2; exit 1 ;;
  esac
  local i=0
  while [ "$i" -lt 30 ]; do
    docker exec "$DB_CONTAINER" pg_isready -q 2>/dev/null && return 0
    sleep 1; i=$((i+1))
  done
  echo "ERROR: PostgreSQL in $DB_CONTAINER did not become ready" >&2
  exit 1
}

stop_db() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1 && \
    docker stop "$DB_CONTAINER" >/dev/null 2>&1 && echo "db: stopped $DB_CONTAINER"
}

start_one() {
  local svc="$1"
  if pid_alive "$svc"; then echo "$svc already running (pid $(cat "$RUN/$svc.pid"))"; return 0; fi
  if lsof -iTCP:"$(port_for "$svc")" -sTCP:LISTEN -P -n >/dev/null 2>&1; then
    echo "ERROR: port $(port_for "$svc") already in use; cannot start $svc" >&2
    return 1
  fi
  mkdir -p "$RUN"
  cd "$ROOT" || return 1
  case "$svc" in
    query)
      nohup "$VENV/bin/python" -m semantic_query_service --host 127.0.0.1 --port "$QUERY_PORT" \
        >"$RUN/query.log" 2>&1 &
      ;;
    parser)
      SEMANTIC_QUERY_URL="http://127.0.0.1:$QUERY_PORT" \
        nohup "$VENV/bin/python" -m intent_parser --host 127.0.0.1 --port "$PARSER_PORT" \
        >"$RUN/parser.log" 2>&1 &
      ;;
    ui)
      ANALYTICS_INTENT_URL="http://127.0.0.1:$PARSER_PORT" \
        nohup "$VENV/bin/python" -m streamlit run streamlit-analytics-client/src/analytics_client/app.py \
        --server.address 127.0.0.1 --server.port "$UI_PORT" --server.headless true \
        >"$RUN/ui.log" 2>&1 &
      ;;
  esac
  echo $! >"$RUN/$svc.pid"
  local i=0
  while [ "$i" -lt "$READY_WAIT_SECONDS" ]; do
    if is_ready "$svc"; then echo "$svc ready"; return 0; fi
    if ! pid_alive "$svc"; then echo "ERROR: $svc exited; see $RUN/$svc.log" >&2; return 1; fi
    sleep 1; i=$((i+1))
  done
  echo "ERROR: $svc not ready after ${READY_WAIT_SECONDS}s; see $RUN/$svc.log" >&2
  return 1
}

stop_one() {
  local svc="$1" f="$RUN/$1.pid"
  if pid_alive "$svc"; then
    local pid; pid="$(cat "$f")"
    kill "$pid" 2>/dev/null
    local i=0
    while kill -0 "$pid" 2>/dev/null && [ "$i" -lt 10 ]; do sleep 1; i=$((i+1)); done
    kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    echo "$svc stopped"
  else
    echo "$svc not running"
  fi
  rm -f "$f"
}

status_one() {
  local svc="$1" state
  if is_ready "$svc"; then state=ready
  elif pid_alive "$svc"; then state=starting
  else state=stopped; fi
  echo "$svc: $state"
}

cmd_start() {
  local with_db=1
  for a in "$@"; do [ "$a" = "--no-db" ] && with_db=0; done
  check_venv; load_env
  [ "$with_db" -eq 1 ] && start_db
  command -v aws >/dev/null 2>&1 && ! aws sts get-caller-identity >/dev/null 2>&1 && \
    echo "WARN: AWS credentials not valid; the intent parser will fail on questions. Run: aws sso login" >&2
  for svc in "${SERVICES[@]}"; do
    start_one "$svc" || { echo "aborting start; stopping anything already started" >&2; cmd_stop; exit 1; }
  done
  echo "all services ready; UI at http://127.0.0.1:$UI_PORT"
}

cmd_stop() {
  local stop_db=0
  for a in "$@"; do [ "$a" = "--stop-db" ] && stop_db=1; done
  for ((i=${#SERVICES[@]}-1; i>=0; i--)); do stop_one "${SERVICES[$i]}"; done
  [ "$stop_db" -eq 1 ] && stop_db
  return 0
}

cmd_status() {
  for svc in "${SERVICES[@]}"; do status_one "$svc"; done
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    echo "db: $(docker inspect -f '{{if .State.Running}}running{{else}}stopped{{end}}' "$DB_CONTAINER" 2>/dev/null || echo missing)"
  else
    echo "db: docker unavailable"
  fi
}

[ $# -ge 1 ] || usage
action="$1"; shift
case "$action" in
  start)   cmd_start "$@" ;;
  stop)    cmd_stop "$@" ;;
  restart) cmd_stop "$@"; cmd_start "$@" ;;
  status)  cmd_status ;;
  *) usage ;;
esac
