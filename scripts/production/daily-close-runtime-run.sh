#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/admin/projects/arbitrage-runtime
LOG_DIR="$ROOT/runtime_data/logs"
mkdir -p "$LOG_DIR"
cd "$ROOT"
export RUNTIME_DATA_ROOT="$ROOT/runtime_data"
export PYTHONPATH="$ROOT"
.venv/bin/python -m runtime.deployment_gate --data-root "$RUNTIME_DATA_ROOT"

exec 9>/tmp/arbitrage-daily-close-runtime.lock
if ! flock -n 9; then
  echo "[$(date '+%F %T %Z')] SKIP: daily close runtime is already running"
  exit 0
fi

echo "[$(date '+%F %T %Z')] START light daily close update (no Full V2 research)"

# 20:45 is deliberately the LIGHT daily close lane.
# Information semantic audit + expensive research are isolated in the 03:00 lane.
set -a
source .runtime_env
set +a

response_file=$(mktemp)
http_code=$(curl -sS --retry 3 --retry-delay 2 -o "$response_file" -w '%{http_code}' \
  -X POST 'http://127.0.0.1:7080/api/opportunity/full-runs' \
  -H 'Content-Type: application/json' \
  --data '{"source_mode":"CLOSE","run_research":false,"ai_execution_mode":"AUTO_API","research_batch_limit":5,"max_research_rounds":1}') || {
    rc=$?
    echo "[$(date '+%F %T %Z')] FAIL: cannot reach runtime API rc=$rc"
    rm -f "$response_file"
    exit "$rc"
  }

body=$(cat "$response_file")
rm -f "$response_file"

if [[ "$http_code" == "409" ]]; then
  if grep -Eq '不是交易日|已经完成|已经在运行中' <<<"$body"; then
    echo "[$(date '+%F %T %Z')] SKIP: $body"
    exit 0
  fi
  echo "[$(date '+%F %T %Z')] FAIL: runtime rejected close request: $body"
  exit 1
fi

if [[ "$http_code" != "200" ]]; then
  echo "[$(date '+%F %T %Z')] FAIL: HTTP $http_code: $body"
  exit 1
fi

job_id=$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read())["job_id"])' <<<"$body")
echo "[$(date '+%F %T %Z')] CLOSE runtime started: $job_id"

# Keep the cron process attached long enough to record the terminal outcome.
for _ in $(seq 1 240); do
  sleep 15
  status_body=$(curl -fsS "http://127.0.0.1:7080/api/runs/$job_id") || continue
  status=$(python3 -c 'import json,sys; print(json.loads(sys.stdin.read()).get("status",""))' <<<"$status_body")
  case "$status" in
    PASS)
      echo "[$(date '+%F %T %Z')] PASS: $job_id"
      exit 0
      ;;
    FAIL|NEEDS_REVIEW)
      echo "[$(date '+%F %T %Z')] FAIL: $job_id status=$status"
      echo "$status_body"
      exit 1
      ;;
    WAITING_FOR_CHAT)
      echo "[$(date '+%F %T %Z')] FAIL: AUTO_API run unexpectedly waits for Chat: $job_id"
      exit 1
      ;;
  esac
done

echo "[$(date '+%F %T %Z')] FAIL: timeout waiting for $job_id"
exit 1
