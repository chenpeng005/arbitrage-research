#!/usr/bin/env bash
set -euo pipefail
BASE=/home/admin/projects/etf-primary-shadow
APP="$BASE/current"
STATE="$BASE/runtime_data"
LOCK="$BASE/runner.lock"
mkdir -p "$STATE" "$BASE/logs" "$BASE/web/data"
/usr/bin/flock -n "$LOCK" /bin/bash -lc "cd '$APP' && PYTHONPATH='$APP' /usr/bin/python3 -m runtime.etf_primary.runtime_cycle --state-dir '$STATE' --timeout 20 --max-age-seconds 604800 && /usr/bin/python3 '$BASE/refresh_web_data.py'"
