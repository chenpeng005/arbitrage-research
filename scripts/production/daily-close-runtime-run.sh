#!/usr/bin/env bash
set -euo pipefail

# LEGACY / MANUAL-ONLY WRAPPER
#
# The CURRENT production schedule is the unified 21:00
# pretrade-unified-runtime-run.sh entry. This historical 20:45 wrapper is kept
# only for explicit compatibility/debugging and must never be put back into
# cron by accident.
if [[ "${ALLOW_LEGACY_DAILY_CLOSE:-0}" != "1" ]]; then
  echo "LEGACY_DISABLED: daily-close-runtime-run.sh is not a CURRENT production entry; use pretrade-unified-runtime-run.sh" >&2
  exit 2
fi

ROOT=/home/admin/projects/arbitrage-runtime
LOG_DIR="$ROOT/runtime_data/logs"
mkdir -p "$LOG_DIR"
cd "$ROOT"
AI_ENV="$ROOT/.convertible_ai_env"
if [[ ! -f "$AI_ENV" ]]; then
  AI_ENV="$ROOT/.runtime_env"
fi
export RUNTIME_DATA_ROOT="$ROOT/runtime_data"
export PYTHONPATH="$ROOT"
.venv/bin/python -m runtime.deployment_gate --data-root "$RUNTIME_DATA_ROOT"

exec 9>/tmp/arbitrage-daily-close-runtime.lock
if ! flock -n 9; then
  echo "[$(date '+%F %T %Z')] SKIP: daily close runtime is already running"
  exit 0
fi

echo "[$(date '+%F %T %Z')] START LEGACY light daily close update"

set -a
source "$AI_ENV"
set +a
# shellcheck disable=SC1091
source "$ROOT/scripts/production/nightly-ai-mode-env.sh"
if [[ "$NIGHTLY_AI_MODE" == "CHAT" ]]; then
  AI_EXECUTION_MODE="INTERACTIVE_CHAT"
else
  AI_EXECUTION_MODE="AUTO_API"
fi

response_file=$(mktemp)
http_code=$(curl -sS --retry 3 --retry-delay 2 -o "$response_file" -w '%{http_code}' \
  -X POST 'http://127.0.0.1:7080/api/opportunity/full-runs' \
  -H 'Content-Type: application/json' \
  --data "{\"source_mode\":\"CLOSE\",\"run_research\":false,\"ai_execution_mode\":\"$AI_EXECUTION_MODE\",\"research_batch_limit\":5,\"max_research_rounds\":1}") || {
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
      echo "[$(date '+%F %T %Z')] FAIL: run unexpectedly waits for Chat: $job_id"
      exit 1
      ;;
  esac
done

echo "[$(date '+%F %T %Z')] FAIL: timeout waiting for $job_id"
exit 1
