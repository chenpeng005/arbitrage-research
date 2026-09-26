"""Controlled cutover readiness gate for SQLite becoming the primary read source."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

CUTOVER_POLICY_VERSION = "incremental-storage-cutover-policy-v1"
MIN_DISTINCT_FORMAL_PARITY_PASSES = 3

def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def evaluate_cutover_readiness(*, data_root: Path) -> dict[str, Any]:
    status_path = data_root / "registry" / "incremental_storage_status.json"
    sync_path = data_root / "registry" / "latest_incremental_storage_sync.json"
    reasons: list[str] = []
    if not status_path.exists():
        return {
            "policy_version": CUTOVER_POLICY_VERSION,
            "status": "NOT_READY",
            "reasons": ["STORAGE_STATUS_MISSING"],
        }
    state = _read_json(status_path)
    latest = _read_json(sync_path) if sync_path.exists() else {}

    if state.get("status") != "SHADOW_DUAL_WRITE":
        reasons.append("NOT_IN_SHADOW_DUAL_WRITE")
    if state.get("schema_version") != "incremental-runtime-sqlite-schema-v1.2":
        reasons.append("SCHEMA_VERSION_NOT_V1_2")
    if state.get("primary_read_source") != "JSON_RUNTIME":
        reasons.append("PRIMARY_READ_SOURCE_ALREADY_CHANGED")
    if state.get("last_sync_status") != "PASS":
        reasons.append("LAST_SHADOW_SYNC_NOT_PASS")
    if state.get("last_state_parity_status") != "PASS":
        reasons.append("STATE_PARITY_NOT_PASS")
    if int(state.get("last_state_mismatch_count") or 0) != 0:
        reasons.append("STATE_PARITY_HAS_MISMATCH")
    if state.get("last_projection_parity_status") != "PASS":
        reasons.append("PROJECTION_PARITY_NOT_PASS")
    if int(state.get("last_projection_mismatch_count") or 0) != 0:
        reasons.append("PROJECTION_PARITY_HAS_MISMATCH")

    streak = int(state.get("parity_consecutive_passes") or 0)
    changed = int(state.get("parity_passes_with_state_change") or 0)
    if streak < MIN_DISTINCT_FORMAL_PARITY_PASSES:
        reasons.append(
            f"NEED_{MIN_DISTINCT_FORMAL_PARITY_PASSES}_DISTINCT_FORMAL_PARITY_PASSES"
        )
    if changed < 1:
        reasons.append("NEED_AT_LEAST_ONE_FORMAL_PASS_WITH_STATE_CHANGE")

    projection = latest.get("projection_parity") or {}
    state_parity = latest.get("parity") or {}
    if projection.get("status") not in (None, "PASS"):
        reasons.append("LATEST_PROJECTION_ARTIFACT_NOT_PASS")
    if state_parity.get("status") not in (None, "PASS"):
        reasons.append("LATEST_STATE_PARITY_ARTIFACT_NOT_PASS")

    ready = not reasons
    return {
        "policy_version": CUTOVER_POLICY_VERSION,
        "status": "READY" if ready else "NOT_READY",
        "reasons": reasons,
        "requirements": {
            "min_distinct_formal_parity_passes": MIN_DISTINCT_FORMAL_PARITY_PASSES,
            "require_at_least_one_state_change_pass": True,
            "state_parity_mismatch": 0,
            "projection_parity_mismatch": 0,
            "rollback_required_before_cutover": True,
        },
        "current": {
            "parity_consecutive_passes": streak,
            "parity_passes_with_state_change": changed,
            "last_state_parity_status": state.get("last_state_parity_status"),
            "last_state_mismatch_count": state.get("last_state_mismatch_count"),
            "last_projection_parity_status": state.get("last_projection_parity_status"),
            "last_projection_mismatch_count": state.get("last_projection_mismatch_count"),
            "primary_read_source": state.get("primary_read_source"),
        },
        "next_action": (
            "PREPARE_ROLLBACK_AND_READ_SOURCE_SWITCH"
            if ready
            else "CONTINUE_SHADOW_DUAL_WRITE"
        ),
    }

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]