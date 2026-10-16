#!/usr/bin/env bash
# Resolve the shared nightly AI execution mode without relying on cron-injected
# environment variables. Explicit test/override paths remain supported.
NIGHTLY_AI_MODE_FILE="${NIGHTLY_AI_MODE_FILE:-/home/admin/projects/server-control/state/nightly-ai-mode.env}"

if [[ -r "$NIGHTLY_AI_MODE_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$NIGHTLY_AI_MODE_FILE"
  set +a
fi

NIGHTLY_AI_MODE="${NIGHTLY_AI_MODE:-API}"
NIGHTLY_AI_MODE="${NIGHTLY_AI_MODE^^}"
case "$NIGHTLY_AI_MODE" in
  CHAT)
    export NIGHTLY_AI_MODE
    export AI_EXECUTION_CHANNEL=CHAT
    export CHAT_PROVIDER_ROOT="${CHAT_PROVIDER_ROOT:-/home/admin/projects/server-control/state/nightly-chat-bridge}"
    ;;
  API)
    export NIGHTLY_AI_MODE
    export AI_EXECUTION_CHANNEL=API
    ;;
  *)
    echo "invalid NIGHTLY_AI_MODE=$NIGHTLY_AI_MODE (expected CHAT or API)" >&2
    return 2 2>/dev/null || exit 2
    ;;
esac
