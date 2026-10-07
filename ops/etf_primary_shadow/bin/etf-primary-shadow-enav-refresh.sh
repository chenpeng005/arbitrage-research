#!/usr/bin/env bash
set -euo pipefail
BASE=/home/admin/projects/etf-primary-shadow
LOCK="$BASE/enav-refresh.lock"
mkdir -p "$BASE/logs" "$BASE/aux_cache"
/usr/bin/flock -n "$LOCK" /usr/bin/python3 "$BASE/refresh_enav.py"
