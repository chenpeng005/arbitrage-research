#!/usr/bin/env bash
set -euo pipefail
ROOT="${ARBITRAGE_RUNTIME_ROOT:-/home/admin/projects/arbitrage-runtime}"
export PYTHONPATH="$ROOT"
export RUNTIME_DATA_ROOT="${RUNTIME_DATA_ROOT:-$ROOT/runtime_data}"
exec "$ROOT/.venv/bin/python" -m runtime.intelligence_radar.xueqiu_shadow_archive --hot-pages 4 --page-size 20
