#!/usr/bin/env bash
set -euo pipefail

LIVE_ROOT="${LIVE_ROOT:-/home/admin/projects/arbitrage-runtime}"
STAGE_ROOT="${STAGE_ROOT:?STAGE_ROOT is required}"
APP_COMMIT_SHA="${APP_COMMIT_SHA:?APP_COMMIT_SHA is required}"
export LIVE_ROOT APP_COMMIT_SHA
BACKUP_ROOT="${BACKUP_ROOT:-/home/admin/deploy-backups/arbitrage-runtime}"
PY="${LIVE_ROOT}/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "missing runtime python: $PY" >&2
  exit 2
fi
if [[ ! -d "$STAGE_ROOT/runtime" ]]; then
  echo "invalid staging tree: $STAGE_ROOT" >&2
  exit 2
fi

echo "[deploy] validate staged source $APP_COMMIT_SHA"
if grep -R "\[executed on device:"   "$STAGE_ROOT/runtime" "$STAGE_ROOT/scripts/production"   --include='*.py' --include='*.sh' -n; then
  echo "source contamination detected" >&2
  exit 3
fi

PYTHONPATH="$STAGE_ROOT" "$PY" -m py_compile   "$STAGE_ROOT/runtime/opportunity/research_trigger.py"   "$STAGE_ROOT/runtime/opportunity/full_runtime_controller.py"   "$STAGE_ROOT/runtime/opportunity/incremental_information_controller.py"   "$STAGE_ROOT/runtime/web/app.py"

for pattern in test_daily_research_decoupling_v1.py test_token_cost_gate_v1.py test_reminder_policy_v2.py; do
  PYTHONPATH="$STAGE_ROOT" "$PY" -m unittest discover -s "$STAGE_ROOT/tests" -p "$pattern" -v
done

ts=$(date -u +%Y%m%dT%H%M%SZ)
backup="$BACKUP_ROOT/$ts-$APP_COMMIT_SHA"
mkdir -p "$backup"
cp -a "$LIVE_ROOT/runtime" "$backup/runtime"
cp -a "$LIVE_ROOT/scripts" "$backup/scripts"
cp -a "$LIVE_ROOT/tests" "$backup/tests"
cp -a "$LIVE_ROOT/runtime_data/deployment_manifest.json"   "$backup/deployment_manifest.json"

rollback() {
  rc=$?
  if [[ $rc -eq 0 ]]; then
    return
  fi
  echo "[deploy] FAIL rc=$rc; restoring previous source"
  rm -rf "$LIVE_ROOT/runtime" "$LIVE_ROOT/scripts" "$LIVE_ROOT/tests"
  cp -a "$backup/runtime" "$LIVE_ROOT/runtime"
  cp -a "$backup/scripts" "$LIVE_ROOT/scripts"
  cp -a "$backup/tests" "$LIVE_ROOT/tests"
  cp -a "$backup/deployment_manifest.json"     "$LIVE_ROOT/runtime_data/deployment_manifest.json"
  if [[ -x /home/admin/bin/arbitrage-web-runtime-start.sh ]]; then
    /home/admin/bin/arbitrage-web-runtime-start.sh || true
  fi
  exit "$rc"
}
trap rollback ERR

echo "[deploy] install staged source"
rm -rf "$LIVE_ROOT/runtime" "$LIVE_ROOT/scripts" "$LIVE_ROOT/tests"
cp -a "$STAGE_ROOT/runtime" "$LIVE_ROOT/runtime"
cp -a "$STAGE_ROOT/scripts" "$LIVE_ROOT/scripts"
cp -a "$STAGE_ROOT/tests" "$LIVE_ROOT/tests"

install -m 0755   "$LIVE_ROOT/scripts/production/web-runtime-start.sh"   /home/admin/bin/arbitrage-web-runtime-start.sh
install -m 0755   "$LIVE_ROOT/scripts/production/information-runtime-run.sh"   /home/admin/bin/information-runtime-run.sh
install -m 0755   "$LIVE_ROOT/scripts/production/pretrade-unified-runtime-run.sh"   /home/admin/bin/pretrade-unified-runtime-run.sh

"$PY" - <<'PY'
import datetime, json, os, pathlib
root = pathlib.Path(os.environ.get(
    "LIVE_ROOT", "/home/admin/projects/arbitrage-runtime"
))
path = root / "runtime_data" / "deployment_manifest.json"
obj = json.loads(path.read_text(encoding="utf-8"))
obj["application_commit_sha"] = os.environ["APP_COMMIT_SHA"]
obj["deployed_at"] = datetime.datetime.now(
    datetime.timezone.utc
).isoformat()
obj["deployment_method"] = (
    "GitHub Actions automatic application deployment; "
    "Knowledge snapshot unchanged"
)
tmp = path.with_suffix(".tmp")
tmp.write_text(
    json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
tmp.replace(path)
PY

cd "$LIVE_ROOT"
export PYTHONPATH="$LIVE_ROOT"
export RUNTIME_DATA_ROOT="$LIVE_ROOT/runtime_data"
"$PY" -m runtime.deployment_gate --data-root "$RUNTIME_DATA_ROOT"

/home/admin/bin/arbitrage-web-runtime-start.sh
curl -fsS http://127.0.0.1:7080/api/health >/dev/null
curl -fsS http://127.0.0.1:7080/api/opportunity/information-status >/dev/null

mkdir -p "$LIVE_ROOT/runtime_data/deployment"
"$PY" - <<'PY'
import datetime, json, os, pathlib
root = pathlib.Path(os.environ.get(
    "LIVE_ROOT", "/home/admin/projects/arbitrage-runtime"
))
out = root / "runtime_data" / "deployment" / "latest_app_auto_deploy.json"
out.write_text(json.dumps({
    "status": "PASS",
    "application_commit_sha": os.environ["APP_COMMIT_SHA"],
    "deployed_at": datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat(),
    "method": "github-actions-ssh-staged-validated",
}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

trap - ERR
echo "[deploy] PASS $APP_COMMIT_SHA"
