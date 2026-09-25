"""Queue helpers for Path Research pending / evidence-hold semantics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


HOLD_STATUSES = {"NEEDS_EVIDENCE", "UNRESOLVED"}


def task_id_from_trigger(trigger_key: str) -> str:
    digest = hashlib.sha256(trigger_key.encode("utf-8")).hexdigest()[:16]
    return f"research_{digest}"


def evidence_pack_sha256(data_root: Path, task_id: str) -> str | None:
    """Stable semantic fingerprint for evidence-dependent rerun gating.

    Runtime timestamps and App deployment metadata are intentionally excluded so
    rebuilding an identical Evidence Pack does not wake a held research task.
    Knowledge commit remains included because a canonical rule change can
    legitimately require re-research even when raw facts are unchanged.
    """
    path = data_root / "research_evidence" / f"{task_id}.json"
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    semantic = {
        "evidence_pack_version": payload.get("evidence_pack_version"),
        "task_id": payload.get("task_id"),
        "trigger_key": payload.get("trigger_key"),
        "bond_code": payload.get("bond_code"),
        "path_id": payload.get("path_id"),
        "market_cutoff": payload.get("market_cutoff"),
        "knowledge_commit_sha": payload.get("knowledge_commit_sha"),
        "sources": payload.get("sources"),
        "facts": payload.get("facts"),
        "coverage": payload.get("coverage"),
        "missing_or_deferred": payload.get("missing_or_deferred"),
    }
    canonical = json.dumps(
        semantic,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def pending_item_is_runnable(
    item: dict[str, Any],
    data_root: Path,
) -> tuple[bool, str]:
    status = str(item.get("research_status") or "PENDING")
    if status not in HOLD_STATUSES:
        return True, "READY"

    task_id = task_id_from_trigger(str(item["trigger_key"]))
    current_sha = evidence_pack_sha256(data_root, task_id)
    hold_sha = item.get("hold_evidence_sha256")

    if current_sha is None:
        return False, "HOLD_EVIDENCE_PACK_MISSING"
    if not hold_sha:
        return True, "READY_NO_HOLD_HASH"
    if current_sha != hold_sha:
        return True, "READY_EVIDENCE_CHANGED"
    return False, "HOLD_WAITING_EVIDENCE"


def make_hold_queue_item(
    *,
    task: dict[str, Any],
    result: dict[str, Any],
    evidence_sha256: str | None,
    updated_at: str,
) -> dict[str, Any]:
    trigger = task.get("trigger_context") or {}
    return {
        "bond_code": str(task["bond_code"]).zfill(6),
        "bond_name": task["bond_name"],
        "path_id": task["path_id"],
        "economic_status": "KEEP",
        "keep_episode_id": task.get("keep_episode_id"),
        "current_event_state": trigger.get("current_event_state"),
        "trigger_key": task["trigger_key"],
        "trigger_reason": trigger.get("trigger_reason"),
        "last_triggered_at": trigger.get("last_triggered_at"),
        "research_status": result["research_status"],
        "last_path_result_id": result["path_result_id"],
        "hold_evidence_sha256": evidence_sha256,
        "hold_reason": result["research_status"],
        "updated_at": updated_at,
    }


def merge_hold_item(
    pending_payload: dict[str, Any],
    hold_item: dict[str, Any],
) -> None:
    items = pending_payload.setdefault("pending_tasks", [])
    trigger_key = hold_item["trigger_key"]
    for idx, item in enumerate(items):
        if item.get("trigger_key") == trigger_key:
            items[idx] = hold_item
            return
    items.append(hold_item)
