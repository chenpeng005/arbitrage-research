#!/usr/bin/env bash
set -euo pipefail
BASE=/home/admin/projects/etf-primary-shadow
APP="$BASE/current"
STATE="$BASE/runtime_data"
LOCK="$BASE/runner.lock"
RELAY_GIT="$BASE/relay_git"
TMP=/tmp/etf-primary-relay-runtime
PORT=8876
mkdir -p "$STATE" "$BASE/logs" "$BASE/web/data"

exec 9>"$LOCK"
/usr/bin/flock -n 9 || exit 0

if [ ! -d "$RELAY_GIT" ]; then
  /usr/bin/git init --bare "$RELAY_GIT" >/dev/null
  /usr/bin/git --git-dir="$RELAY_GIT" remote add origin https://github.com/chenpeng005/arbitrage-research.git
fi
/usr/bin/git --git-dir="$RELAY_GIT" fetch --depth=1 origin etf-primary-data-relay >/dev/null 2>&1
rm -rf "$TMP"
mkdir -p "$TMP"
/usr/bin/git --git-dir="$RELAY_GIT" archive FETCH_HEAD etf_primary_relay | /usr/bin/tar -x -C "$TMP"

/usr/bin/python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$TMP" >"$BASE/logs/local-relay-http.log" 2>&1 &
HPID=$!
cleanup() {
  kill "$HPID" 2>/dev/null || true
  rm -rf "$TMP"
}
trap cleanup EXIT
sleep 0.3

cd "$APP"
PYTHONPATH="$APP" /usr/bin/python3 -m runtime.etf_primary.runtime_cycle \
  --state-dir "$STATE" \
  --relay-base-url "http://127.0.0.1:$PORT/etf_primary_relay" \
  --timeout 20 \
  --max-age-seconds 604800
/usr/bin/python3 "$BASE/ui_guard.py" run -- /usr/bin/python3 "$BASE/refresh_web_data.py"
