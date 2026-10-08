#!/usr/bin/env bash
set -euo pipefail
BASE=/home/admin/projects/etf-primary-shadow
LOCK="$BASE/web-refresh.lock"
mkdir -p "$BASE/logs" "$BASE/web/data"
/usr/bin/flock -n "$LOCK" /usr/bin/python3 "$BASE/ui_guard.py" run -- /usr/bin/python3 "$BASE/refresh_web_data.py"
