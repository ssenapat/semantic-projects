#!/usr/bin/env bash
# Local runner for the NL analytics stack (CONTRACT §8). All services bind 127.0.0.1.
# Usage: run-local.sh {setup|start|stop|status} [--stub-llm]
# Secrets are read only from the environment (PGPASSWORD; the LLM uses AWS Bedrock via your AWS credentials, so no API key); values are never printed.
set -u

TS="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$TS/.venv"
RUN="$TS/.run"
SERVICES=(query parser retriever ui)

QUERY_HOST="${QUERY_HOST:-127.0.0.1}";         QUERY_PORT="${QUERY_PORT:-8082}"
PARSER_HOST="${PARSER_HOST:-127.0.0.1}";       PARSER_PORT="${PARSER_PORT:-8081}"
RETRIEVER_HOST="${RETRIEVER_HOST:-127.0.0.1}"; RETRIEVER_PORT="${RETRIEVER_PORT:-8080}"
UI_PORT="${UI_PORT:-8501}"
READY_WAIT_SECONDS=60

usage() { echo "usage: $(basename "$0") {setup|start|stop|status} [--stub-llm]" >&2; exit 2; }

folder() {
  case "$1" in
    query) echo "$TS/semantic-query-service" ;;
    parser) echo "$TS/llm-intent-parser-service" ;;
    retriever) echo "$TS/semantic-retrieval-service" ;;
    ui) echo "$TS/streamlit-analytics-client" ;;
  esac
}
ready_url() {
  case "$1" in
    query) echo "http://$QUERY_HOST:$QUERY_PORT/health/ready" ;;
    parser) echo "http://$PARSER_HOST:$PARSER_PORT/health/ready" ;;
    retriever) echo "http://$RETRIEVER_HOST:$RETRIEVER_PORT/health/ready" ;;
    ui) echo "http://127.0.0.1:$UI_PORT/_stcore/health" ;;
  esac
}
is_ready() { curl -fsS -m 3 -o /dev/null "$(ready_url "$1")" 2>/dev/null; }
pid_alive() { local f="$RUN/$1.pid"; [ -f "$f" ] && kill -0 "$(cat "$f")" 2>/dev/null; }

cmd_setup() {
  local py="${PYTHON:-}" c
  if [ -z "$py" ]; then
    for c in python3.13 python3.12 python3.11 python3; do
      if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
        py="$c"; break
      fi
    done
  fi
  [ -n "$py" ] && command -v "$py" >/dev/null 2>&1 || { echo "ERROR: no Python >= 3.11 found (set PYTHON to one)" >&2; exit 1; }
  "$py" -m venv "$VENV" || exit 1
  "$VENV/bin/pip" install --upgrade pip >/dev/null || exit 1
  "$VENV/bin/pip" install -e "$TS/semantic-retrieval-service[dev]" \
    -e "$TS/llm-intent-parser-service[test]" \
    -e "$TS/semantic-query-service[dev]" || exit 1
  "$VENV/bin/pip" install -r "$TS/streamlit-analytics-client/requirements.txt" || exit 1
  echo "setup complete: $VENV"
}

start_one() {
  local svc="$1" dir; dir="$(folder "$svc")"
  if pid_alive "$svc"; then echo "$svc already running (pid $(cat "$RUN/$svc.pid"))"; return 0; fi
  (
    cd "$dir" || exit 1
    case "$svc" in
      query) exec "$VENV/bin/uvicorn" semantic_query_service.app.main:app --host "$QUERY_HOST" --port "$QUERY_PORT" ;;
      parser) exec "$VENV/bin/uvicorn" llm_intent_parser_service.app.main:app --host "$PARSER_HOST" --port "$PARSER_PORT" ;;
      retriever) exec "$VENV/bin/uvicorn" semantic_retrieval_service.app.main:app --host "$RETRIEVER_HOST" --port "$RETRIEVER_PORT" ;;
      ui) exec "$VENV/bin/streamlit" run streamlit_analytics_client/app.py --server.address 127.0.0.1 --server.port "$UI_PORT" --server.headless true ;;
    esac
  ) >"$RUN/$svc.log" 2>&1 &
  echo $! >"$RUN/$svc.pid"
  local i=0
  while [ "$i" -lt "$READY_WAIT_SECONDS" ]; do
    if ! pid_alive "$svc"; then echo "ERROR: $svc exited; see $RUN/$svc.log" >&2; return 1; fi
    if is_ready "$svc"; then echo "$svc ready"; return 0; fi
    sleep 1; i=$((i + 1))
  done
  echo "ERROR: $svc not ready after ${READY_WAIT_SECONDS}s; see $RUN/$svc.log" >&2
  return 1
}

cmd_start() {
  local stub=0
  [ "${1:-}" = "--stub-llm" ] && stub=1
  [ -n "${PGPASSWORD:-}" ] || { echo "ERROR: environment variable PGPASSWORD is not set" >&2; exit 1; }
  if [ "$stub" -eq 0 ]; then
    if [ "${PARSER_LLM_CLIENT:-bedrock}" = "anthropic" ]; then
      [ -n "${ANTHROPIC_API_KEY:-}" ] || { echo "ERROR: environment variable ANTHROPIC_API_KEY is not set (or use --stub-llm)" >&2; exit 1; }
    else
      [ -n "${AWS_REGION:-${AWS_DEFAULT_REGION:-}}" ] || { echo "ERROR: set AWS_REGION (and BEDROCK_MODEL_ID), or use --stub-llm" >&2; exit 1; }
      [ -n "${BEDROCK_MODEL_ID:-}" ] || { echo "ERROR: set BEDROCK_MODEL_ID to a Bedrock model/inference-profile id, or use --stub-llm" >&2; exit 1; }
      case "$BEDROCK_MODEL_ID" in
        us.*|eu.*|apac.*|au.*|jp.*|ca.*|us-gov.*|global.*|arn:*) ;;
        *) echo "ERROR: BEDROCK_MODEL_ID='$BEDROCK_MODEL_ID' has no inference-profile prefix (us., eu., apac., global.)." >&2
           echo "       Bedrock rejects on-demand calls to a bare model id (HTTP 502 llm_provider_failure)." >&2
           echo "       Try e.g. us.$BEDROCK_MODEL_ID  (list: aws bedrock list-inference-profiles --region \$AWS_REGION)" >&2
           exit 1 ;;
      esac
    fi
  else
    export PARSER_LLM_CLIENT=stub
  fi
  [ -x "$VENV/bin/uvicorn" ] || { echo "ERROR: virtualenv missing; run: $0 setup" >&2; exit 1; }
  export SEMANTIC_LAYER_SRC="${SEMANTIC_LAYER_SRC:-$TS/semantic-layer/src}"
  export SEMANTIC_LAYER_DEFINITIONS_DIR="${SEMANTIC_LAYER_DEFINITIONS_DIR:-$TS/semantic-layer/definitions}"
  export UI_TIMEOUT_SECONDS="${UI_TIMEOUT_SECONDS:-90}"
  mkdir -p "$RUN"
  local svc
  for svc in "${SERVICES[@]}"; do start_one "$svc" || exit 1; done
  echo "all services ready; UI at http://127.0.0.1:$UI_PORT"
}

cmd_stop() {
  local svc f
  for svc in "${SERVICES[@]}"; do
    f="$RUN/$svc.pid"
    if [ -f "$f" ]; then
      pid_alive "$svc" && kill "$(cat "$f")" 2>/dev/null && echo "$svc stopped"
      rm -f "$f"
    fi
  done
}

cmd_status() {
  local svc state
  for svc in "${SERVICES[@]}"; do
    if is_ready "$svc"; then state=ready
    elif pid_alive "$svc"; then state=starting
    else state=stopped; fi
    echo "$svc: $state"
  done
}

[ $# -ge 1 ] || usage
action="$1"; shift
case "$action" in
  setup) cmd_setup ;;
  start) cmd_start "${1:-}" ;;
  stop) cmd_stop ;;
  status) cmd_status ;;
  *) usage ;;
esac
