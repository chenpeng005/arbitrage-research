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

mkdir -p runtime_data/logs

pids=$(ps -ef | awk '/runtime.web.app:app.*(8010|7080)/ && !/awk/ {print $2}')
if [[ -n "$pids" ]]; then
  kill $pids
  sleep 1
fi

nohup .venv/bin/uvicorn runtime.web.app:app --host 0.0.0.0 --port 8010 \
  > runtime_data/logs/web-8010.log 2>&1 < /dev/null &

nohup .venv/bin/uvicorn runtime.web.app:app --host 127.0.0.1 --port 7080 --no-proxy-headers \
  > runtime_data/logs/web-7080.log 2>&1 < /dev/null &

sleep 2
curl -fsS http://127.0.0.1:7080/api/opportunity/information-status >/dev/null
echo "web runtime started with .runtime_env"
