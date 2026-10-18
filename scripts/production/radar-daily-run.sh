#!/usr/bin/env bash
set -euo pipefail
ROOT="${ARBITRAGE_RUNTIME_ROOT:-/home/admin/projects/arbitrage-runtime}"
cd "$ROOT"
AI_ENV="$ROOT/.radar_ai_env"
if [[ ! -f "$AI_ENV" ]]; then
  AI_ENV="$ROOT/.runtime_env"
fi
if [[ -f "$AI_ENV" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$AI_ENV"
  set +a
fi
# Radar owns its transport state. Stock and convertible mode changes must not
# alter this launcher.
# shellcheck disable=SC1091
source "$ROOT/scripts/production/radar-ai-mode-env.sh"
export PYTHONPATH="$ROOT"
export RUNTIME_DATA_ROOT="${RUNTIME_DATA_ROOT:-$ROOT/runtime_data}"

if [[ "$RADAR_AI_MODE" == "CHAT" ]]; then
  export RADAR_CHAT_JISILU_BATCH_SIZE="${RADAR_CHAT_JISILU_BATCH_SIZE:-24}"
  export RADAR_CHAT_XUEQIU_BATCH_SIZE="${RADAR_CHAT_XUEQIU_BATCH_SIZE:-60}"
  exec "$ROOT/.venv/bin/python" -m runtime.intelligence_radar.chat_runner
fi

exec "$ROOT/.venv/bin/python" -m runtime.intelligence_radar.run_all --run-mode LIVE
