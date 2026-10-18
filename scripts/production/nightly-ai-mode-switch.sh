#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-}"
STATE_DIR="${NIGHTLY_AI_MODE_STATE_DIR:-/home/admin/projects/server-control/state}"
STATE_FILE="$STATE_DIR/nightly-ai-mode.env"
CHAT_ROOT="${CHAT_PROVIDER_ROOT:-$STATE_DIR/nightly-chat-bridge}"
RUNTIME_ROOT="${ARBITRAGE_RUNTIME_ROOT:-/home/admin/projects/arbitrage-runtime}"
RECOVERY_DATA_ROOT="${NIGHTLY_AI_RECOVERY_DATA_ROOT:-$RUNTIME_ROOT/runtime_data}"
RECOVERY_PY="${NIGHTLY_AI_RECOVERY_PY:-$RUNTIME_ROOT/.venv/bin/python}"
RECOVERY_REASON="${NIGHTLY_AI_RECOVERY_REASON:-NIGHTLY_MODE_SWITCHED_TO_API}"

case "${MODE,,}" in
  chat) MODE=CHAT ;;
  api) MODE=API ;;
  *)
    echo "usage: $0 chat|api" >&2
    exit 2
    ;;
esac

recover_abandoned_chat_tasks() {
  if [[ ! -x "$RECOVERY_PY" ]]; then
    echo "chat-to-api recovery python missing: $RECOVERY_PY" >&2
    return 3
  fi
  if [[ ! -f "$RUNTIME_ROOT/runtime/opportunity/chat_to_api_recovery.py" ]]; then
    echo "chat-to-api recovery module missing under: $RUNTIME_ROOT" >&2
    return 3
  fi
  if [[ ! -d "$RECOVERY_DATA_ROOT" ]]; then
    echo "chat-to-api recovery data root missing: $RECOVERY_DATA_ROOT" >&2
    return 3
  fi

  local dry summary semantic_count path_count blocker_count blocker_reasons
  dry="$(
    PYTHONPATH="$RUNTIME_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
      "$RECOVERY_PY" -m runtime.opportunity.chat_to_api_recovery \
      --data-root "$RECOVERY_DATA_ROOT"
  )"
  summary="$(
    "$RECOVERY_PY" -c '
import json, sys
obj=json.load(sys.stdin)
counts=obj.get("candidate_counts") or {}
unsafe_exact={
    "ACTIVE_CLAIM",
    "INVALID_CLAIM_FILE",
    "CHAT_TASK_INVALID_JSON",
    "DUPLICATE_WAITING_CHAT_SOURCE",
    "SEMANTIC_SOURCE_TASK_MISSING",
    "SEMANTIC_SOURCE_IDENTITY_MISMATCH",
    "SEMANTIC_LEDGER_ROW_MISSING",
    "SEMANTIC_LEDGER_KIND_MISMATCH",
    "SEMANTIC_LEDGER_TASK_ID_MISMATCH",
    "PATH_SOURCE_TASK_MISSING",
    "PATH_SOURCE_IDENTITY_MISMATCH",
    "PATH_PENDING_CHAT_TASK_MISMATCH",
    "PATH_TRIGGER_STATE_MISSING",
}
unsafe_prefixes=("PATH_PENDING_MATCH_COUNT_",)
reasons=[]
for row in obj.get("skipped") or []:
    reason=str(row.get("reason") or "")
    if reason in unsafe_exact or any(reason.startswith(p) for p in unsafe_prefixes):
        reasons.append(reason)
print(int(counts.get("semantic") or 0))
print(int(counts.get("path") or 0))
print(len(reasons))
print(",".join(sorted(set(reasons))))
' <<<"$dry"
  )"
  semantic_count="$(sed -n '1p' <<<"$summary")"
  path_count="$(sed -n '2p' <<<"$summary")"
  blocker_count="$(sed -n '3p' <<<"$summary")"
  blocker_reasons="$(sed -n '4p' <<<"$summary")"

  echo "CHAT_TO_API_RECOVERY_DRY_RUN semantic=$semantic_count path=$path_count blockers=$blocker_count"
  if [[ "$blocker_count" != "0" ]]; then
    echo "CHAT_TO_API_RECOVERY_BLOCKED reasons=$blocker_reasons" >&2
    return 4
  fi

  if [[ "$semantic_count" == "0" && "$path_count" == "0" ]]; then
    echo "CHAT_TO_API_RECOVERY=NOOP"
    return 0
  fi

  local applied applied_status
  applied="$(
    PYTHONPATH="$RUNTIME_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
      "$RECOVERY_PY" -m runtime.opportunity.chat_to_api_recovery \
      --data-root "$RECOVERY_DATA_ROOT" \
      --apply \
      --expect-semantic "$semantic_count" \
      --expect-path "$path_count" \
      --reason "$RECOVERY_REASON"
  )"
  applied_status="$(
    "$RECOVERY_PY" -c 'import json,sys; print(json.load(sys.stdin).get("status") or "")' \
      <<<"$applied"
  )"
  if [[ "$applied_status" != "APPLIED" ]]; then
    echo "CHAT_TO_API_RECOVERY unexpected status=$applied_status" >&2
    return 5
  fi
  echo "CHAT_TO_API_RECOVERY=APPLIED semantic=$semantic_count path=$path_count"
}

# The mode file is written only after API recovery succeeds. This makes the
# channel switch fail closed when a live Chat claim or inconsistent residual
# state makes automatic handoff unsafe.
if [[ "$MODE" == "API" ]]; then
  recover_abandoned_chat_tasks
fi

mkdir -p "$STATE_DIR" "$CHAT_ROOT/tasks"
umask 077
TMP="$(mktemp "$STATE_DIR/.nightly-ai-mode.env.XXXXXX")"
trap 'rm -f "$TMP"' EXIT
cat > "$TMP" <<EOF
# Managed by nightly-ai-mode-switch.sh
NIGHTLY_AI_MODE=$MODE
CHAT_PROVIDER_ROOT=$CHAT_ROOT
EOF
mv "$TMP" "$STATE_FILE"
trap - EXIT

echo "NIGHTLY_AI_MODE=$MODE"
echo "STATE_FILE=$STATE_FILE"
