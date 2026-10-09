#!/usr/bin/env bash
set -euo pipefail

# Canonical Runtime listens on 127.0.0.1:7080.
# Public 8443 is provided by Caddy; legacy 8010 is an nginx compatibility proxy.
sudo -n systemctl restart arbitrage-web-local.service

for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:7080/api/opportunity/information-status >/dev/null 2>&1; then
    echo "web runtime ready"
    exit 0
  fi
  sleep 1
done

echo "web runtime failed readiness check" >&2
sudo -n systemctl status arbitrage-web-local.service --no-pager || true
exit 1
