#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/admin/projects/arbitrage-runtime
cd "$ROOT"
AI_ENV="$ROOT/.convertible_ai_env"
if [[ ! -f "$AI_ENV" ]]; then
  AI_ENV="$ROOT/.runtime_env"
fi
set -a
source "$AI_ENV"
set +a
export RUNTIME_DATA_ROOT="$ROOT/runtime_data"
export PYTHONPATH="$ROOT"
mkdir -p runtime_data/logs
.venv/bin/python -m runtime.deployment_gate --data-root "$RUNTIME_DATA_ROOT"
exec flock -n /tmp/arbitrage-information-runtime.lock .venv/bin/python -m runtime.opportunity.incremental_information_cli "$@"
