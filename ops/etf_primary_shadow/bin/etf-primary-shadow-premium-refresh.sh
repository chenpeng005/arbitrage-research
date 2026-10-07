#!/usr/bin/env bash
set -euo pipefail
BASE=/home/admin/projects/etf-primary-shadow
LOCK="$BASE/premium-refresh.lock"
mkdir -p "$BASE/logs"
/usr/bin/flock -n "$LOCK" /bin/bash -lc "ETF_REFRESH_REFERENCE_PREMIUM=1 /usr/bin/python3 '$BASE/refresh_web_data.py'"
