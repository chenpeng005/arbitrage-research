from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .daily_reminder import LofDailyReminderStore
from .estimate_freshness_audit import load_freshness_audit
from .estimate_validation import load_estimate_validation_summary
from .nav_freshness import nav_freshness_summary
from .r2c_profile_summary import load_r2c_t1_profile_summary
from .shadow_registry import load_shadow_registry
from .snapshot_store import LofSnapshotStore


MODULE_DIR = Path(__file__).resolve().parent
STATIC_DIR = MODULE_DIR / "static"
DATA_ROOT = Path(
    os.environ.get(
        "LOF_RUNTIME_DATA_ROOT",
        MODULE_DIR.parents[1] / "runtime_data" / "lof",
    )
)

store = LofSnapshotStore(DATA_ROOT)
reminder_store = LofDailyReminderStore(DATA_ROOT)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
app = FastAPI(title="LOF Opportunity Monitor")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/reminder")
def reminder_page():
    return FileResponse(STATIC_DIR / "reminder.html")


@app.get("/api/lof/daily-reminder")
def daily_reminder():
    requested_date = datetime.now(SHANGHAI_TZ).date().isoformat()
    today = reminder_store.load_daily_reminder(requested_date)
    if today is not None:
        return {
            "status": "TODAY",
            "requested_date": requested_date,
            "reminder": today,
        }
    latest = reminder_store.load_latest_daily_reminder()
    return {
        "status": "LATEST" if latest is not None else "UNAVAILABLE",
        "requested_date": requested_date,
        "reminder": latest,
    }


@app.get("/api/health")
def health():
    snapshot = store.load_latest()
    nav_freshness = nav_freshness_summary(snapshot)
    return {
        "status": "PASS" if snapshot is not None else "NO_SNAPSHOT",
        "snapshot_id": snapshot.get("snapshot_id") if snapshot else None,
        "generated_at": snapshot.get("generated_at") if snapshot else None,
        "universe_count": snapshot.get("universe_count") if snapshot else None,
        "collector_status": snapshot.get("collector_status") if snapshot else None,
        "nav_freshness": nav_freshness,
    }


@app.get("/api/lof/snapshot")
def latest_snapshot():
    snapshot = store.load_latest()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="LOF snapshot unavailable")
    result = store.enrich_with_last_estimates(snapshot)
    result["nav_freshness"] = nav_freshness_summary(snapshot)
    return result


@app.get("/api/lof/estimate-validation")
def estimate_validation_summary():
    return load_estimate_validation_summary(DATA_ROOT)


@app.get("/api/lof/estimate-freshness-audit")
def estimate_freshness_audit():
    snapshot = store.load_latest()
    if snapshot is None:
        raise HTTPException(status_code=503, detail="LOF snapshot unavailable")
    enriched = store.enrich_with_last_estimates(snapshot)
    return load_freshness_audit(
        DATA_ROOT,
        snapshot=enriched,
    )


@app.get("/api/lof/r2c-t1-profile")
def r2c_t1_profile_summary():
    return load_r2c_t1_profile_summary(DATA_ROOT)


@app.get("/api/lof/shadow-registry")
def shadow_registry():
    return load_shadow_registry(
        DATA_ROOT,
        main_snapshot=store.load_latest(),
    )
