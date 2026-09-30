"""Non-blocking shadow SQLite sync + parity audit for Full Runtime."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_market_change_writer import (
    capture_market_path_state,
    persist_market_transition_changes,
)
from runtime.opportunity.incremental_storage_parity import audit_json_sqlite_parity
from runtime.opportunity.incremental_storage_projection import audit_projection_parity
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
    previous_changed_passes = int(storage_status.get("parity_passes_with_state_change") or 0)

    try:
        previous_market_states = capture_market_path_state(target_db=db_path)
        sync = shadow_sync_current_runtime(data_root=data_root, target_db=db_path)
        market_changes = persist_market_transition_changes(
            target_db=db_path,
            previous_states=previous_market_states,
        )
        parity = audit_json_sqlite_parity(data_root=data_root, target_db=db_path)
        projection_parity = audit_projection_parity(
            data_root=data_root,
            target_db=db_path,
        )
        status = (
            "PASS"
            if sync.get("status") == "PASS"
            and market_changes.get("status") == "PASS"
            and parity.get("status") == "PASS"
            and projection_parity.get("status") == "PASS"
            else "FAIL"
        )

        current_snapshot = parity.get("market_snapshot_id")
        changed_passes = previous_changed_passes
        if status == "PASS":
            if current_snapshot and current_snapshot != previous_snapshot:
                streak = previous_streak + 1
                if int((sync.get("stats") or {}).get("state_version_increments") or 0) > 0:
                    changed_passes += 1
            else:
                streak = previous_streak
        else:
            streak = 0

        result = {
            "shadow_stage_version": SHADOW_STAGE_VERSION,
            "status": status,
            "sync": sync,
            "market_changes": market_changes,
            "parity": parity,
            "projection_parity": projection_parity,
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
                "last_state_parity_status": parity.get("status"),
                "last_state_mismatch_count": parity.get("mismatch_count"),
                "last_projection_parity_status": projection_parity.get("status"),
                "last_projection_mismatch_count": projection_parity.get("mismatch_count"),
                "last_state_version_increments": (sync.get("stats") or {}).get("state_version_increments"),
                "last_research_state_version_increments": (sync.get("stats") or {}).get("research_state_version_increments"),
                "last_market_transition_count": market_changes.get("detected_transition_count"),
                "last_market_notification_group_count": market_changes.get("notification_group_count"),
                "parity_consecutive_passes": streak,
                "parity_passes_with_state_change": changed_passes,
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

