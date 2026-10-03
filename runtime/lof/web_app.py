from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
app = FastAPI(title="LOF Opportunity Monitor")

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def nav_freshness_summary(snapshot: dict | None) -> dict:
    rows = (snapshot or {}).get("rows") or []
    r1_rows = [
        row
        for row in rows
        if row.get("resolver_class") == "R1_DOMESTIC_INDEX"
    ]
    fresh_rows = [
        row for row in r1_rows
        if row.get("official_nav_lag_label") == "T-1"
    ]
    lagging_rows = [
        row for row in r1_rows
        if row.get("official_nav_lag_label") != "T-1"
    ]
    expected_dates = sorted(
        {
            str(row.get("official_nav_date"))
            for row in fresh_rows
            if row.get("official_nav_date")
        }
    )
    return {
        "status": (
            "PASS"
            if r1_rows and len(fresh_rows) == len(r1_rows)
            else "WARN"
        ),
        "r1_total": len(r1_rows),
        "r1_t1_count": len(fresh_rows),
        "r1_lagging_count": len(lagging_rows),
        "expected_nav_date": (
            expected_dates[-1] if expected_dates else None
        ),
        "lagging_codes": [
            str(row.get("code") or "")
            for row in lagging_rows
            if row.get("code")
        ],
    }


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


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


@app.get("/api/lof/r2c-t1-profile")
def r2c_t1_profile_summary():
    return load_r2c_t1_profile_summary(DATA_ROOT)


@app.get("/api/lof/shadow-registry")
def shadow_registry():
    return load_shadow_registry(
        DATA_ROOT,
        main_snapshot=store.load_latest(),
    )
