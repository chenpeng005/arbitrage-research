#!/usr/bin/env bash
# Resolve only the Radar system's AI execution mode.
RADAR_AI_MODE_FILE="${RADAR_AI_MODE_FILE:-/home/admin/projects/server-control/state/radar-ai-mode.env}"

if [[ -r "$RADAR_AI_MODE_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$RADAR_AI_MODE_FILE"
  set +a
fi

RADAR_AI_MODE="${RADAR_AI_MODE:-API}"
RADAR_AI_MODE="${RADAR_AI_MODE^^}"
case "$RADAR_AI_MODE" in
  CHAT)
    export RADAR_AI_MODE
    export AI_EXECUTION_CHANNEL=CHAT
    export CHAT_PROVIDER_ROOT="${CHAT_PROVIDER_ROOT:-/home/admin/projects/server-control/state/nightly-chat-bridge}"
    export CHAT_PROVIDER_SCOPE=radar
    ;;
  API)
    export RADAR_AI_MODE
    export AI_EXECUTION_CHANNEL=API
    unset CHAT_PROVIDER_SCOPE || true
    ;;
  *)
    echo "invalid RADAR_AI_MODE=$RADAR_AI_MODE (expected CHAT or API)" >&2
    return 2 2>/dev/null || exit 2
    ;;
esac
