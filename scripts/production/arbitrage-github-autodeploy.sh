#!/usr/bin/env bash
set -euo pipefail

REPO_API="${REPO_API:-https://api.github.com/repos/chenpeng005/arbitrage-research}"
REPO_GIT_URL="${REPO_GIT_URL:-https://github.com/chenpeng005/arbitrage-research.git}"
WORKFLOW_FILE="${WORKFLOW_FILE:-deploy-convertible-runtime.yml}"
LIVE_ROOT="${LIVE_ROOT:-/home/admin/projects/arbitrage-runtime}"
STATE_ROOT="${STATE_ROOT:-/home/admin/.local/state/arbitrage-autodeploy}"
STAGING_ROOT="${STAGING_ROOT:-/home/admin/deploy-staging/arbitrage-runtime}"
LOCK_DIR="${LOCK_DIR:-/tmp/arbitrage-github-autodeploy.lock.d}"
VALIDATION_RETRY_SECONDS="${VALIDATION_RETRY_SECONDS:-300}"
VALIDATION_BLOCK_RETRY_SECONDS="${VALIDATION_BLOCK_RETRY_SECONDS:-900}"

mkdir -p "$STATE_ROOT" "$STAGING_ROOT"
acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" > "$LOCK_DIR/pid"
    return 0
  fi

  local old_pid=""
  old_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || true)"
  if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "[autodeploy] another deployment check is running: pid=$old_pid"
    return 1
  fi

  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR"
  printf '%s\n' "$$" > "$LOCK_DIR/pid"
}
if ! acquire_lock; then
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT

# HEAD discovery is intentionally Git-native rather than GitHub REST. The timer
# runs every minute; polling /branches/main through the unauthenticated REST API
# would consume the shared 60-request/hour IP budget even when nothing changed.
if ! remote_line="$(
  timeout 20s git ls-remote --exit-code "$REPO_GIT_URL" refs/heads/main 2>/dev/null
)"; then
  echo "[autodeploy] Git HEAD check timed out/failed; defer to next timer"
  exit 0
fi
remote_sha="$(awk 'NR==1 {print $1}' <<<"$remote_line")"
if [[ ! "$remote_sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "[autodeploy] invalid remote SHA: $remote_sha" >&2
  exit 2
fi

current_sha="$(
  python3 - "$LIVE_ROOT/runtime_data/deployment_manifest.json" <<'PY'
import json, pathlib, sys
p=pathlib.Path(sys.argv[1])
try:
    print(json.loads(p.read_text(encoding="utf-8")).get("application_commit_sha") or "")
except Exception:
    print("")
PY
)"

if [[ "$remote_sha" == "$current_sha" ]]; then
  exit 0
fi

echo "[autodeploy] new main commit detected: current=$current_sha remote=$remote_sha"

retry_file="$STATE_ROOT/validation-retry-$remote_sha.epoch"
now_epoch="$(date +%s)"
if [[ -f "$retry_file" ]]; then
  retry_epoch="$(tr -dc '0-9' < "$retry_file")"
  if [[ "$retry_epoch" =~ ^[0-9]+$ ]] && (( now_epoch < retry_epoch )); then
    echo "[autodeploy] validation API backoff active until epoch=$retry_epoch remote=$remote_sha"
    exit 0
  fi
fi

schedule_retry() {
  local delay="$1"
  local reason="$2"
  local next_epoch=$(( $(date +%s) + delay ))
  printf '%s\n' "$next_epoch" > "$retry_file"
  echo "[autodeploy] validation retry scheduled in ${delay}s reason=$reason remote=$remote_sha"
}

workflow_file="$STATE_ROOT/workflow-$remote_sha.json"
headers_file="$STATE_ROOT/workflow-$remote_sha.headers"
http_code="$(
  curl -sS --connect-timeout 5 --max-time 20 \
    -D "$headers_file" \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "$REPO_API/actions/workflows/$WORKFLOW_FILE/runs?branch=main&head_sha=$remote_sha&per_page=5" \
    -o "$workflow_file" \
    -w '%{http_code}' || true
)"

if [[ "$http_code" != "200" ]]; then
  delay="$VALIDATION_RETRY_SECONDS"
  remaining="$(awk -F': *' 'tolower($1)=="x-ratelimit-remaining" {gsub("\\r","",$2); print $2}' "$headers_file" 2>/dev/null | tail -n1)"
  reset_epoch="$(awk -F': *' 'tolower($1)=="x-ratelimit-reset" {gsub("\\r","",$2); print $2}' "$headers_file" 2>/dev/null | tail -n1)"
  retry_after="$(awk -F': *' 'tolower($1)=="retry-after" {gsub("\\r","",$2); print $2}' "$headers_file" 2>/dev/null | tail -n1)"
  now_epoch="$(date +%s)"
  if [[ "$retry_after" =~ ^[0-9]+$ ]] && (( retry_after > delay )); then
    delay="$retry_after"
  fi
  if [[ "$remaining" == "0" && "$reset_epoch" =~ ^[0-9]+$ && "$reset_epoch" -gt "$now_epoch" ]]; then
    until_reset=$(( reset_epoch - now_epoch + 15 ))
    if (( until_reset > delay )); then
      delay="$until_reset"
    fi
  fi
  schedule_retry "$delay" "github-http-$http_code"
  echo "[autodeploy] GitHub validation API unavailable http=$http_code remaining=${remaining:-unknown} reset=${reset_epoch:-unknown}; defer"
  rm -f "$workflow_file" "$headers_file"
  exit 0
fi
rm -f "$headers_file"

gate="$(
  python3 - "$remote_sha" "$workflow_file" <<'PY'
import json, pathlib, sys
sha=sys.argv[1]
obj=json.loads(pathlib.Path(sys.argv[2]).read_text(encoding="utf-8"))
runs=[
    r for r in obj.get("workflow_runs", [])
    if r.get("head_sha")==sha and r.get("event") in {"push","workflow_dispatch"}
]
if not runs:
    print("WAIT:NO_RUN")
    raise SystemExit
runs.sort(
    key=lambda r: (
        int(r.get("run_number") or 0),
        r.get("created_at") or "",
    ),
    reverse=True,
)
r=runs[0]
status=r.get("status")
conclusion=r.get("conclusion")
if status!="completed":
    print(f"WAIT:{status or 'UNKNOWN'}")
elif conclusion=="success":
    print("PASS")
else:
    print(f"BLOCK:{conclusion or 'UNKNOWN'}")
PY
)"
rm -f "$workflow_file"

case "$gate" in
  PASS)
    rm -f "$retry_file"
    ;;
  WAIT:*)
    schedule_retry "$VALIDATION_RETRY_SECONDS" "$gate"
    echo "[autodeploy] validation not complete: $gate"
    exit 0
    ;;
  BLOCK:*)
    schedule_retry "$VALIDATION_BLOCK_RETRY_SECONDS" "$gate"
    echo "[autodeploy] validation failed; deployment blocked: $gate"
    exit 0
    ;;
  *)
    echo "[autodeploy] unexpected validation gate: $gate" >&2
    exit 3
    ;;
esac

stage="$STAGING_ROOT/$remote_sha"
rm -rf "$stage"
mkdir -p "$stage"
archive="$STATE_ROOT/$remote_sha.tar.gz"

curl -fsSL --connect-timeout 5 --max-time 30 \
  "https://codeload.github.com/chenpeng005/arbitrage-research/tar.gz/$remote_sha" \
  -o "$archive"
tar -xzf "$archive" -C "$stage" --strip-components=1
rm -f "$archive"

echo "[autodeploy] validation PASS; deploying $remote_sha"
APP_COMMIT_SHA="$remote_sha" STAGE_ROOT="$stage" LIVE_ROOT="$LIVE_ROOT" bash "$stage/scripts/production/deploy-from-github.sh"

printf '%s\n' "$remote_sha" > "$STATE_ROOT/last_successful_sha"
date -Is > "$STATE_ROOT/last_successful_at"
rm -rf "$stage"
echo "[autodeploy] PASS $remote_sha"
