#!/usr/bin/env bash
set -euo pipefail
ROOT=/home/admin/projects/arbitrage-runtime
cd "$ROOT"
set -a
source .runtime_env
set +a
export RUNTIME_DATA_ROOT="$ROOT/runtime_data"
export PYTHONPATH="$ROOT"
export RUNTIME_TRUST_LOCAL_PROXY_AUTH=1
exec .venv/bin/uvicorn runtime.web.app:app --host 0.0.0.0 --port 8010
