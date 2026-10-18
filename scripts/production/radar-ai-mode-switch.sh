#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
STATE_DIR="${RADAR_AI_MODE_STATE_DIR:-/home/admin/projects/server-control/state}"
STATE_FILE="${RADAR_AI_MODE_FILE:-$STATE_DIR/radar-ai-mode.env}"
CHAT_ROOT="${CHAT_PROVIDER_ROOT:-$STATE_DIR/nightly-chat-bridge}"

case "${MODE,,}" in
  chat) MODE=CHAT ;;
  api) MODE=API ;;
  *)
    echo "usage: $0 chat|api" >&2
    exit 2
    ;;
esac

# Radar mode switching is intentionally Radar-only. Historical Bridge tasks may
# remain for audit, but this switch never inspects or modifies stock/convertible
# business state.
mkdir -p "$STATE_DIR" "$CHAT_ROOT/tasks"
umask 077
TMP="$(mktemp "$STATE_DIR/.radar-ai-mode.env.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<EOF
# Managed by radar-ai-mode-switch.sh
RADAR_AI_MODE=$MODE
CHAT_PROVIDER_ROOT=$CHAT_ROOT
EOF
mv "$TMP" "$STATE_FILE"
trap - EXIT

echo "RADAR_AI_MODE=$MODE"
echo "STATE_FILE=$STATE_FILE"
