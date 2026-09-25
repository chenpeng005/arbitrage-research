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
    Knowledge/App deployment metadata are intentionally excluded. Canonical
    changes are governed separately; this fingerprint answers only whether the
    Evidence Pack itself contains materially different research evidence.
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



def restore_unresolved_ledger_to_pending(data_root: Path) -> dict[str, Any]:
    """Rehydrate historical unresolved ledger entries into evidence HOLD queue."""
    ledger_path = data_root / "registry" / "research_ledger.json"
    pending_path = data_root / "registry" / "pending_research_tasks.json"
    if not ledger_path.exists():
        return {"restored": 0, "skipped": 0, "reason": "NO_LEDGER"}

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    pending = (
        json.loads(pending_path.read_text(encoding="utf-8"))
        if pending_path.exists()
        else {"pending_tasks": []}
    )

    restored = 0
    skipped = 0
    for entry in ledger.get("results", {}).values():
        status = str(entry.get("research_status") or "")
        if status not in HOLD_STATUSES:
            continue

        task_id = str(entry.get("task_id") or "")
        result_path = Path(str(entry.get("result_path") or ""))
        task_path = data_root / "research_tasks" / f"{task_id}.json"
        if not task_id or not task_path.exists() or not result_path.exists():
            skipped += 1
            continue

        task = json.loads(task_path.read_text(encoding="utf-8"))
        result = json.loads(result_path.read_text(encoding="utf-8"))
        hold_item = make_hold_queue_item(
            task=task,
            result=result,
            evidence_sha256=evidence_pack_sha256(data_root, task_id),
            updated_at=entry.get("completed_at") or "",
        )
        before = len(pending.get("pending_tasks", []))
        merge_hold_item(pending, hold_item)
        after = len(pending.get("pending_tasks", []))
        if after > before:
            restored += 1
        else:
            restored += 1

    pending["updated_at"] = (
        max(
            [
                str(x.get("updated_at") or "")
                for x in pending.get("pending_tasks", [])
            ]
            or [""]
        )
    )
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    pending_path.write_text(
        json.dumps(pending, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "restored": restored,
        "skipped": skipped,
        "pending_total": len(pending.get("pending_tasks", [])),
    }
