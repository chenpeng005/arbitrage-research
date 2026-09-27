"""Bridge SQLite Event-driven PATH_RESEARCH intents into the existing V2 task pipeline."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage import connect
from runtime.opportunity.research_task_builder import build_path_research_tasks

BRIDGE_VERSION = "incremental-event-research-bridge-v1"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

def _current_registry_path(data_root: Path) -> Path:
    ptr=_read_json(data_root/"registry"/"latest_economic_path_registry.json")
    path=Path(str(ptr["result_path"]))
    if not path.is_absolute():
        path=data_root.parent/path
    return path

def promote_event_path_research(
    *,
    data_root: Path,
    target_db: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    """Promote at most one pending Event Full-V2 trigger per bond/path.

    Multiple simultaneous Event triggers on one path are fail-closed and require
    coalescing before any one can overwrite the active research trigger.
    """
    conn=connect(target_db)
    trigger_state_path=data_root/"registry"/"research_trigger_state.json"
    pending_path=data_root/"registry"/"pending_research_tasks.json"
    trigger_state=_read_json(trigger_state_path)
    pending=_read_json(pending_path)
    existing_pending=pending.get("pending_tasks",[])

    rows=list(conn.execute(
        """SELECT rtl.trigger_key,rtl.bond_code,rtl.path_id,rtl.source_event_update_id,
                  rtl.research_action,rtl.task_kind,rtl.status,rtl.emitted_at,
                  eu.occurred_at,ef.event_family
           FROM research_trigger_ledger rtl
           LEFT JOIN event_update eu ON eu.event_update_id=rtl.source_event_update_id
           LEFT JOIN event_family ef ON ef.event_family_id=eu.event_family_id
           WHERE rtl.task_kind='PATH_RESEARCH' AND rtl.status='PENDING'
           ORDER BY rtl.emitted_at,rtl.trigger_key"""
    ))
    grouped=defaultdict(list)
    for row in rows:
        grouped[(row["bond_code"],row["path_id"])].append(row)

    promoted=[]
    conflicts=[]
    deferred=[]
    now=_now()
    try:
        for (bond_code,path_id),items in sorted(grouped.items()):
            if len(items)>1:
                conflicts.append({
                    "bond_code":bond_code,"path_id":path_id,
                    "reason":"MULTIPLE_PENDING_EVENT_FULL_V2_TRIGGERS",
                    "trigger_keys":[x["trigger_key"] for x in items],
                })
                continue

            row=items[0]
            state_key=f"{bond_code}:{path_id}"
            state_row=(trigger_state.get("paths") or {}).get(state_key)
            if state_row is None:
                conflicts.append({
                    "bond_code":bond_code,"path_id":path_id,
                    "reason":"LEGACY_TRIGGER_STATE_MISSING",
                    "trigger_key":row["trigger_key"],
                })
                continue
            if state_row.get("economic_status")!="KEEP":
                conn.execute(
                    "UPDATE research_trigger_ledger SET status=?,updated_at=? WHERE trigger_key=?",
                    ("SUPPRESSED_ECONOMIC_DROP",now,row["trigger_key"]),
                )
                deferred.append({
                    "bond_code":bond_code,"path_id":path_id,
                    "reason":"ECONOMIC_PATH_NOT_KEEP",
                    "trigger_key":row["trigger_key"],
                })
                continue

            active_other=[
                x for x in existing_pending
                if str(x.get("bond_code")).zfill(6)==bond_code
                and x.get("path_id")==path_id
                and x.get("trigger_key")!=row["trigger_key"]
            ]
            if active_other:
                conflicts.append({
                    "bond_code":bond_code,"path_id":path_id,
                    "reason":"OTHER_PENDING_RESEARCH_ALREADY_ACTIVE",
                    "trigger_key":row["trigger_key"],
                    "active_trigger_keys":[x.get("trigger_key") for x in active_other],
                })
                continue

            family=row["event_family"] or "UNKNOWN"
            trigger_reason=f"EVENT:{family}"
            state_row["last_trigger_key"]=row["trigger_key"]
            state_row["last_trigger_reason"]=trigger_reason
            state_row["last_triggered_at"]=row["emitted_at"] or now
            state_row["research_status"]="PENDING"
            state_row["last_trigger_source"]="EVENT"
            state_row["source_event_update_id"]=row["source_event_update_id"]
            state_row["updated_at"]=now

            existing_same=next(
                (x for x in existing_pending if x.get("trigger_key")==row["trigger_key"]),
                None,
            )
            item={
                "bond_code":bond_code,
                "bond_name":state_row.get("bond_name"),
                "path_id":path_id,
                "economic_status":"KEEP",
                "keep_episode_id":state_row.get("keep_episode_id"),
                "current_event_state":state_row.get("current_event_state"),
                "trigger_key":row["trigger_key"],
                "trigger_reason":trigger_reason,
                "last_triggered_at":row["emitted_at"] or now,
                "research_status":"PENDING",
                "trigger_source":"EVENT",
                "source_event_update_id":row["source_event_update_id"],
                "research_cutoff":row["occurred_at"],
            }
            if existing_same is None:
                existing_pending.append(item)
            else:
                existing_same.update(item)

            conn.execute(
                "UPDATE research_trigger_ledger SET status=?,updated_at=? WHERE trigger_key=?",
                ("PROMOTED_TO_PENDING_QUEUE",now,row["trigger_key"]),
            )
            conn.execute(
                """UPDATE research_binding
                   SET validity_status='UPDATE_PENDING',
                       reuse_reason='EVENT_FULL_V2_PENDING',
                       validity_basis_json=?
                   WHERE bond_code=? AND path_id=? AND is_current=1""",
                (
                    json.dumps({
                        "bridge_version": BRIDGE_VERSION,
                        "trigger_key": row["trigger_key"],
                        "source_event_update_id": row["source_event_update_id"],
                        "event_family": family,
                    }, ensure_ascii=False, sort_keys=True),
                    bond_code,
                    path_id,
                ),
            )
            promoted.append(item)

        registry_path=_current_registry_path(data_root)
        registry=_read_json(registry_path)
        pending["economic_registry_run_id"]=registry["run_id"]
        pending["market_snapshot_id"]=registry["market_snapshot_id"]
        pending["updated_at"]=now
        pending["pending_tasks"]=existing_pending
        trigger_state["updated_at"]=now
        _write_json(trigger_state_path,trigger_state)
        _write_json(pending_path,pending)
        conn.commit()

        built=None
        if promoted:
            built=build_path_research_tasks(
                economic_registry_path=registry_path,
                pending_queue_path=pending_path,
                data_root=data_root,
                deployment=deployment,
            )
            task_by_trigger={x["trigger_key"]:x for x in built.get("tasks",[])}
            conn=connect(target_db)
            try:
                for item in promoted:
                    task=task_by_trigger.get(item["trigger_key"])
                    if not task:
                        continue
                    conn.execute(
                        """UPDATE research_trigger_ledger
                           SET status=?,task_id=?,updated_at=?
                           WHERE trigger_key=?""",
                        ("READY_FOR_AI_RESEARCH",task["task_id"],_now(),item["trigger_key"]),
                    )
                conn.commit()
            finally:
                conn.close()

        return {
            "bridge_version":BRIDGE_VERSION,
            "status":"PASS" if not conflicts else "NEEDS_REVIEW",
            "promoted_count":len(promoted),
            "conflict_count":len(conflicts),
            "deferred_count":len(deferred),
            "promoted":promoted,
            "conflicts":conflicts,
            "deferred":deferred,
            "task_batch":built,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass

