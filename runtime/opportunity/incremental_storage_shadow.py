"""Non-blocking shadow SQLite sync + parity audit for Full Runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage_parity import audit_json_sqlite_parity
from runtime.opportunity.incremental_storage_sync import shadow_sync_current_runtime

SHADOW_STAGE_VERSION = "incremental-storage-shadow-stage-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_incremental_storage_shadow(*, data_root: Path) -> dict[str, Any]:
    status_path = data_root / "registry" / "incremental_storage_status.json"
    db_path = data_root / "state" / "incremental_runtime.sqlite"
    latest_path = data_root / "registry" / "latest_incremental_storage_sync.json"

    if not status_path.exists() or not db_path.exists():
        result = {
            "shadow_stage_version": SHADOW_STAGE_VERSION,
            "status": "SKIPPED",
            "reason": "SHADOW_STORAGE_NOT_INITIALIZED",
            "completed_at": _now(),
        }
        _write_json(latest_path, result)
        return result

    storage_status = _read_json(status_path)
    previous_snapshot = storage_status.get("last_parity_market_snapshot_id")
    previous_streak = int(storage_status.get("parity_consecutive_passes") or 0)

    try:
        sync = shadow_sync_current_runtime(data_root=data_root, target_db=db_path)
        parity = audit_json_sqlite_parity(data_root=data_root, target_db=db_path)
        status = "PASS" if sync.get("status") == "PASS" and parity.get("status") == "PASS" else "FAIL"

        current_snapshot = parity.get("market_snapshot_id")
        if status == "PASS":
            if current_snapshot and current_snapshot != previous_snapshot:
                streak = previous_streak + 1
            else:
                streak = previous_streak
        else:
            streak = 0

        result = {
            "shadow_stage_version": SHADOW_STAGE_VERSION,
            "status": status,
            "sync": sync,
            "parity": parity,
            "completed_at": _now(),
        }

        storage_status.update(
            {
                "status": "SHADOW_DUAL_WRITE",
                "sqlite_write_mode": "SHADOW_DUAL_WRITE",
                "cutover_state": "PARITY_ACTIVE",
                "last_sync_status": status,
                "last_sync_at": result["completed_at"],
                "last_parity_market_snapshot_id": current_snapshot,
                "parity_consecutive_passes": streak,
                "next_gate": "CONTROLLED_CUTOVER_REVIEW",
            }
        )
        _write_json(status_path, storage_status)
        _write_json(latest_path, result)
        return result
    except Exception as exc:
        result = {
            "shadow_stage_version": SHADOW_STAGE_VERSION,
            "status": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "completed_at": _now(),
        }
        storage_status.update(
            {
                "status": "SHADOW_DUAL_WRITE",
                "sqlite_write_mode": "SHADOW_DUAL_WRITE",
                "cutover_state": "PARITY_ACTIVE",
                "last_sync_status": "FAIL",
                "last_sync_at": result["completed_at"],
                "parity_consecutive_passes": 0,
                "next_gate": "SHADOW_FAILURE_REVIEW",
            }
        )
        _write_json(status_path, storage_status)
        _write_json(latest_path, result)
        return result

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]