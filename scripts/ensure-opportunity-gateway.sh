#!/usr/bin/env bash
set -euo pipefail

CONFIG=/home/admin/.config/arbitrage-runtime/Caddyfile.viewer-admin

exec 9>/tmp/opportunity-gateway.lock
flock -n 9 || exit 0

if curl -fsS http://127.0.0.1:2019/config/ | python3 -c '
import json,sys
cfg=json.load(sys.stdin)
raw=json.dumps(cfg,sort_keys=True)
ok=("caddy-public" in raw and "caddy-admin" in raw)
raise SystemExit(0 if ok else 1)
'; then
  exit 0
fi

echo "[$(date '+%F %T %Z')] opportunity-gateway: restoring viewer/admin gateway"
caddy reload --config "$CONFIG" --adapter caddyfile
