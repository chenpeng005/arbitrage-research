#!/usr/bin/env bash
# Resolve only the convertible system's AI execution mode.
CONVERTIBLE_AI_MODE_FILE="${CONVERTIBLE_AI_MODE_FILE:-/home/admin/projects/server-control/state/convertible-ai-mode.env}"

if [[ -r "$CONVERTIBLE_AI_MODE_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$CONVERTIBLE_AI_MODE_FILE"
  set +a
fi

CONVERTIBLE_AI_MODE="${CONVERTIBLE_AI_MODE:-API}"
CONVERTIBLE_AI_MODE="${CONVERTIBLE_AI_MODE^^}"
case "$CONVERTIBLE_AI_MODE" in
  CHAT)
    export CONVERTIBLE_AI_MODE
    export AI_EXECUTION_CHANNEL=CHAT
    export CHAT_PROVIDER_ROOT="${CHAT_PROVIDER_ROOT:-/home/admin/projects/server-control/state/nightly-chat-bridge}"
    ;;
  API)
    export CONVERTIBLE_AI_MODE
    export AI_EXECUTION_CHANNEL=API
    ;;
  *)
    echo "invalid CONVERTIBLE_AI_MODE=$CONVERTIBLE_AI_MODE (expected CHAT or API)" >&2
    return 2 2>/dev/null || exit 2
    ;;
esac
