#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/admin/projects/arbitrage-runtime
LOG_DIR="$ROOT/runtime_data/logs"
mkdir -p "$LOG_DIR"
cd "$ROOT"

AI_ENV="$ROOT/.convertible_ai_env"
if [[ ! -f "$AI_ENV" ]]; then
  AI_ENV="$ROOT/.runtime_env"
fi
set -a
source "$AI_ENV"
set +a
# Resolve transport from the shared mode file rather than cron-injected env.
# shellcheck disable=SC1091
source "$ROOT/scripts/production/nightly-ai-mode-env.sh"
export RUNTIME_DATA_ROOT="$ROOT/runtime_data"
export PYTHONPATH="$ROOT"
if [[ "$NIGHTLY_AI_MODE" == "CHAT" ]]; then
  AI_EXECUTION_MODE="INTERACTIVE_CHAT"
else
  AI_EXECUTION_MODE="AUTO_API"
fi
export AI_EXECUTION_MODE

.venv/bin/python -m runtime.deployment_gate --data-root "$RUNTIME_DATA_ROOT"

exec 9>/tmp/arbitrage-pretrade-unified.lock
if ! flock -n 9; then
  echo "[$(date '+%F %T %Z')] SKIP: unified update already running"
  exit 0
fi

gate_json=$(
  .venv/bin/python - <<'PY'
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import akshare as ak
import pandas as pd

now = datetime.now(ZoneInfo("Asia/Shanghai"))
today = now.date()
tomorrow = today + timedelta(days=1)
cal = ak.tool_trade_date_hist_sina().copy()
dates = {
    d for d in pd.to_datetime(cal["trade_date"], errors="coerce").dt.date.dropna()
}
latest = max(d for d in dates if d <= today)
print(json.dumps({
    "today": today.isoformat(),
    "tomorrow": tomorrow.isoformat(),
    "tomorrow_is_trade_day": tomorrow in dates,
    "latest_completed_trade_day": latest.isoformat(),
}, ensure_ascii=False))
PY
)

tomorrow_trade=$(python3 -c 'import json,sys; print(str(json.load(sys.stdin)["tomorrow_is_trade_day"]).lower())' <<<"$gate_json")
today=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["today"])' <<<"$gate_json")
market_cutoff=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["latest_completed_trade_day"])' <<<"$gate_json")

if [[ "$tomorrow_trade" != "true" ]]; then
  echo "[$(date '+%F %T %Z')] SKIP: next calendar day is not an A-share trading day: $gate_json"
  exit 0
fi

echo "[$(date '+%F %T %Z')] START unified pre-trade update mode=$AI_EXECUTION_MODE: $gate_json"

poll_job() {
  local job_id="$1"
  local label="$2"
  local last_status=""
  for _ in $(seq 1 2880); do
    sleep 5
    local body
    body=$(curl -fsS "http://127.0.0.1:7080/api/runs/$job_id") || continue
    local status
    status=$(python3 -c 'import json,sys; print(json.load(sys.stdin).get("status",""))' <<<"$body")
    case "$status" in
      PASS|WARNING)
        echo "[$(date '+%F %T %Z')] $label PASS: $job_id"
        return 0
        ;;
      WAITING_FOR_CHAT)
        if [[ "$AI_EXECUTION_MODE" == "INTERACTIVE_CHAT" ]]; then
          if [[ "$last_status" != "$status" ]]; then
            echo "[$(date '+%F %T %Z')] $label WAITING_FOR_CHAT: $job_id"
          fi
          last_status="$status"
          continue
        fi
        echo "[$(date '+%F %T %Z')] $label FAIL: $job_id status=$status"
        echo "$body"
        return 1
        ;;
      FAIL|NEEDS_REVIEW)
        echo "[$(date '+%F %T %Z')] $label FAIL: $job_id status=$status"
        echo "$body"
        return 1
        ;;
      *)
        last_status="$status"
        ;;
    esac
  done
  echo "[$(date '+%F %T %Z')] $label FAIL: timeout $job_id"
  return 1
}

wait_for_new_chat_tasks() {
  local since_epoch="$1"
  [[ "$AI_EXECUTION_MODE" == "INTERACTIVE_CHAT" ]] || return 0
  for _ in $(seq 1 2880); do
    local n
    n=$(.venv/bin/python - "$RUNTIME_DATA_ROOT" "$since_epoch" <<'PY'
import json,sys
from pathlib import Path
root=Path(sys.argv[1])/'chat_tasks'
since=float(sys.argv[2])-5
count=0
if root.exists():
    for p in root.glob('*/chat_task.json'):
        try:
            if p.stat().st_mtime < since:
                continue
            d=json.loads(p.read_text(encoding='utf-8'))
            if d.get('status') in {'WAITING_FOR_CHAT','CLAIMED_BY_CHAT'}:
                count += 1
        except Exception:
            pass
print(count)
PY
)
    if [[ "$n" == "0" ]]; then
      return 0
    fi
    sleep 5
  done
  echo "[$(date '+%F %T %Z')] INFORMATION_RESEARCH FAIL: chat task timeout"
  return 1
}

market_payload=$(printf '{"snapshot_mode":"CLOSE","market_cutoff":"%s","ai_execution_mode":"%s"}' "$market_cutoff" "$AI_EXECUTION_MODE")
market_body=$(curl -fsS -X POST 'http://127.0.0.1:7080/api/market-map-runs' \
  -H 'Content-Type: application/json' \
  --data "$market_payload")
market_job=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$market_body")
poll_job "$market_job" "MARKET_MAP"

core_payload=$(printf '{"source_mode":"LATEST_FORMAL","run_research":false,"ai_execution_mode":"%s","research_batch_limit":5,"max_research_rounds":1}' "$AI_EXECUTION_MODE")
core_body=$(curl -fsS -X POST 'http://127.0.0.1:7080/api/opportunity/full-runs' \
  -H 'Content-Type: application/json' \
  --data "$core_payload")
core_job=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$core_body")
poll_job "$core_job" "OPPORTUNITY_CORE"

information_status="PASS"
information_started=$(date +%s)
if /home/admin/bin/information-runtime-run.sh \
  --target-date "$today" \
  --execution-mode "$AI_EXECUTION_MODE" \
  --path-limit 5; then
  if wait_for_new_chat_tasks "$information_started"; then
    echo "[$(date '+%F %T %Z')] INFORMATION_RESEARCH PASS"
  else
    information_status="WARNING"
  fi
else
  rc=$?
  information_status="WARNING"
  echo "[$(date '+%F %T %Z')] WARNING: information/research enrichment failed rc=$rc; core market/opportunity update remains valid"
fi

if .venv/bin/python -m runtime.opportunity.daily_notification_snapshot \
  --data-root "$RUNTIME_DATA_ROOT" \
  --snapshot-date "$today" \
  --update-status "$information_status"; then
  echo "[$(date '+%F %T %Z')] REMINDER_SNAPSHOT PASS: $today"
else
  snapshot_rc=$?
  echo "[$(date '+%F %T %Z')] WARNING: reminder snapshot failed rc=$snapshot_rc; runtime result remains valid"
fi

echo "[$(date '+%F %T %Z')] PASS unified pre-trade update mode=$AI_EXECUTION_MODE"
