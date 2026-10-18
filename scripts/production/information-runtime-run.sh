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

# The shared nightly mode is the single source of truth for normal Information
# Runtime execution. Legacy callers may still pass --execution-mode; those
# arguments are intentionally ignored here so a stale typed Job cannot bypass
# the shared API/CHAT switch and recreate abandoned CHAT work.
# shellcheck disable=SC1091
source "$ROOT/scripts/production/nightly-ai-mode-env.sh"
if [[ "$NIGHTLY_AI_MODE" == "CHAT" ]]; then
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
      echo "[$(date '+%F %T %Z')] INFO: ignoring legacy --execution-mode=$2; shared NIGHTLY_AI_MODE=$NIGHTLY_AI_MODE is authoritative" >&2
      shift 2
      ;;
    --execution-mode=*)
      echo "[$(date '+%F %T %Z')] INFO: ignoring legacy $1; shared NIGHTLY_AI_MODE=$NIGHTLY_AI_MODE is authoritative" >&2
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
