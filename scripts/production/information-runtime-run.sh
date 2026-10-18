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

# Convertible owns its transport state. Legacy callers may still pass
# --execution-mode; those arguments are intentionally ignored so stale typed
# jobs cannot bypass the convertible-specific API/CHAT switch.
# shellcheck disable=SC1091
source "$ROOT/scripts/production/convertible-ai-mode-env.sh"
if [[ "$CONVERTIBLE_AI_MODE" == "CHAT" ]]; then
  AI_EXECUTION_MODE="INTERACTIVE_CHAT"
else
  AI_EXECUTION_MODE="AUTO_API"
fi
export AI_EXECUTION_MODE

clean_args=()
while (( "$#" )); do
  case "$1" in
    --execution-mode)
      if (( "$#" < 2 )); then
        echo "missing value after --execution-mode" >&2
        exit 2
      fi
      echo "[$(date '+%F %T %Z')] INFO: ignoring legacy --execution-mode=$2; CONVERTIBLE_AI_MODE=$CONVERTIBLE_AI_MODE is authoritative" >&2
      shift 2
      ;;
    --execution-mode=*)
      echo "[$(date '+%F %T %Z')] INFO: ignoring legacy $1; CONVERTIBLE_AI_MODE=$CONVERTIBLE_AI_MODE is authoritative" >&2
      shift
      ;;
    *)
      clean_args+=("$1")
      shift
      ;;
  esac
done

export RUNTIME_DATA_ROOT="$ROOT/runtime_data"
export PYTHONPATH="$ROOT"
mkdir -p runtime_data/logs

.venv/bin/python -m runtime.deployment_gate --data-root "$RUNTIME_DATA_ROOT"
exec flock -n /tmp/arbitrage-information-runtime.lock \
  .venv/bin/python -m runtime.opportunity.incremental_information_cli \
  --execution-mode "$AI_EXECUTION_MODE" \
  "${clean_args[@]}"
