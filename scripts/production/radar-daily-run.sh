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
export PYTHONPATH="$ROOT"
export RUNTIME_DATA_ROOT="${RUNTIME_DATA_ROOT:-$ROOT/runtime_data}"
exec "$ROOT/.venv/bin/python" -m runtime.intelligence_radar.run_all --run-mode LIVE
