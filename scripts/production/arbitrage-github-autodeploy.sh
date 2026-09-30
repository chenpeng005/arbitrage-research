#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/chenpeng005/arbitrage-research.git}"
REPO_API="${REPO_API:-https://api.github.com/repos/chenpeng005/arbitrage-research}"
WORKFLOW_FILE="${WORKFLOW_FILE:-deploy-convertible-runtime.yml}"
LIVE_ROOT="${LIVE_ROOT:-/home/admin/projects/arbitrage-runtime}"
STATE_ROOT="${STATE_ROOT:-/home/admin/.local/state/arbitrage-autodeploy}"
STAGING_ROOT="${STAGING_ROOT:-/home/admin/deploy-staging/arbitrage-runtime}"
LOCK_FILE="${LOCK_FILE:-/tmp/arbitrage-github-autodeploy.lock}"

mkdir -p "$STATE_ROOT" "$STAGING_ROOT"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[autodeploy] another deployment check is running"
  exit 0
fi

remote_sha="$(
  git ls-remote "$REPO_URL" refs/heads/main | awk 'NR==1 {print $1}'
)"
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

workflow_json="$(
  curl -fsSL     -H 'Accept: application/vnd.github+json'     -H 'X-GitHub-Api-Version: 2022-11-28'     "$REPO_API/actions/workflows/$WORKFLOW_FILE/runs?branch=main&head_sha=$remote_sha&per_page=5"
)"

gate="$(
  python3 - "$remote_sha" <<'PY' <<<"$workflow_json"
import json, sys
sha=sys.argv[1]
obj=json.load(sys.stdin)
runs=[
    r for r in obj.get("workflow_runs", [])
    if r.get("head_sha")==sha and r.get("event") in {"push","workflow_dispatch"}
]
if not runs:
    print("WAIT:NO_RUN")
    raise SystemExit
runs.sort(key=lambda r: (int(r.get("run_number") or 0), r.get("created_at") or ""), reverse=True)
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

case "$gate" in
  PASS)
    ;;
  WAIT:*)
    echo "[autodeploy] validation not complete: $gate"
    exit 0
    ;;
  BLOCK:*)
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

curl -fsSL   "https://codeload.github.com/chenpeng005/arbitrage-research/tar.gz/$remote_sha"   -o "$archive"
tar -xzf "$archive" -C "$stage" --strip-components=1
rm -f "$archive"

echo "[autodeploy] validation PASS; deploying $remote_sha"
APP_COMMIT_SHA="$remote_sha" STAGE_ROOT="$stage" LIVE_ROOT="$LIVE_ROOT" bash "$stage/scripts/production/deploy-from-github.sh"

printf '%s\n' "$remote_sha" > "$STATE_ROOT/last_successful_sha"
date -Is > "$STATE_ROOT/last_successful_at"
rm -rf "$stage"
echo "[autodeploy] PASS $remote_sha"
