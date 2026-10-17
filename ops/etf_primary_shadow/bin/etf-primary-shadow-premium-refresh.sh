#!/usr/bin/env bash
set -euo pipefail
BASE=/home/admin/projects/etf-primary-shadow
LOCK="$BASE/premium-refresh.lock"
mkdir -p "$BASE/logs" "$BASE/runtime_data/wechat_snapshot/etf"
/usr/bin/flock -n "$LOCK" /bin/bash -lc "ETF_REFRESH_REFERENCE_PREMIUM=1 /usr/bin/python3 '$BASE/refresh_web_data.py' && /usr/bin/python3 '$BASE/wechat_snapshot.py' --monitor '$BASE/web/data/monitor_view.json' --premium '$BASE/web/data/reference_premium.json' --comparison '$BASE/web/data/comparison.json' --output-root '$BASE/runtime_data/wechat_snapshot/etf'"
