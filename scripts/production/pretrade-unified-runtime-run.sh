#!/usr/bin/env bash
set -euo pipefail

ROOT=/home/admin/projects/arbitrage-runtime
LOG_DIR="$ROOT/runtime_data/logs"
mkdir -p "$LOG_DIR"
cd "$ROOT"

set -a
source .runtime_env
set +a
export RUNTIME_DATA_ROOT="$ROOT/runtime_data"
export PYTHONPATH="$ROOT"

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

echo "[$(date '+%F %T %Z')] START unified pre-trade update: $gate_json"

poll_job() {
  local job_id="$1"
  local label="$2"
  for _ in $(seq 1 360); do
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
      FAIL|NEEDS_REVIEW|WAITING_FOR_CHAT)
        echo "[$(date '+%F %T %Z')] $label FAIL: $job_id status=$status"
        echo "$body"
        return 1
        ;;
    esac
  done
  echo "[$(date '+%F %T %Z')] $label FAIL: timeout $job_id"
  return 1
}

market_body=$(curl -fsS -X POST 'http://127.0.0.1:7080/api/market-map-runs'   -H 'Content-Type: application/json'   --data "{"snapshot_mode":"PRE_TRADE_CLOSE","market_cutoff":"$market_cutoff","ai_execution_mode":"AUTO_API"}")
market_job=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$market_body")
poll_job "$market_job" "MARKET_MAP"

core_body=$(curl -fsS -X POST 'http://127.0.0.1:7080/api/opportunity/full-runs'   -H 'Content-Type: application/json'   --data '{"source_mode":"LATEST_FORMAL","run_research":false,"ai_execution_mode":"AUTO_API","research_batch_limit":5,"max_research_rounds":1}')
core_job=$(python3 -c 'import json,sys; print(json.load(sys.stdin)["job_id"])' <<<"$core_body")
poll_job "$core_job" "OPPORTUNITY_CORE"

if /home/admin/bin/information-runtime-run.sh   --target-date "$today"   --execution-mode AUTO_API   --path-limit 5; then
  echo "[$(date '+%F %T %Z')] INFORMATION_RESEARCH PASS"
else
  rc=$?
  echo "[$(date '+%F %T %Z')] WARNING: information/research enrichment failed rc=$rc; core market/opportunity update remains valid"
fi

echo "[$(date '+%F %T %Z')] PASS unified pre-trade update"
