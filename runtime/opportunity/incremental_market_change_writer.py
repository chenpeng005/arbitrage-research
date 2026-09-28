"""Persist only meaningful market-driven opportunity transitions.

Daily prices live in market_observation. Change Ledger stores state transitions,
not every numeric market tick.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_change_notification import (
    attach_notification_decision,
    coalesce_notification_groups,
    market_change,
)
from runtime.opportunity.incremental_storage import connect, json_text

MARKET_CHANGE_WRITER_VERSION = "incremental-market-change-writer-v2"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def capture_market_path_state(*, target_db: Path) -> dict[str, dict[str, Any]]:
    conn=connect(target_db)
    try:
        rows=conn.execute(
            """SELECT s.bond_code,s.scope_id,s.economic_status,s.payload_json,
                      b.bond_name,s.source_snapshot_id
               FROM scope_state_current s
               JOIN bond_master b ON b.bond_code=s.bond_code
               WHERE s.scope_type='PATH'"""
        )
        out={}
        for row in rows:
            payload=json.loads(row["payload_json"] or "{}")
            trigger_state=payload.get("trigger_state") or {}
            current_identity=payload.get("current_state_identity") or {}
            research_status=(
                trigger_state.get("research_status")
                or current_identity.get("research_status")
            )
            last_result_id=(
                trigger_state.get("last_path_result_id")
                or current_identity.get("last_path_result_id")
            )
            out[f'{row["bond_code"]}:{row["scope_id"]}']={
                "bond_code":row["bond_code"],
                "bond_name":row["bond_name"],
                "path_id":row["scope_id"],
                "economic_status":row["economic_status"],
                "economic_path":payload.get("economic_path") or {},
                "research_status":research_status,
                "research_attention":bool(
                    (research_status and research_status!="NOT_TRIGGERED")
                    or last_result_id
                ),
                "source_snapshot_id":row["source_snapshot_id"],
            }
        return out
    finally:
        conn.close()
def _current_market_path_state(conn) -> dict[str, dict[str, Any]]:
    rows=conn.execute(
        """SELECT s.bond_code,s.scope_id,s.economic_status,s.payload_json,
                  b.bond_name,s.source_snapshot_id
           FROM scope_state_current s
           JOIN bond_master b ON b.bond_code=s.bond_code
           WHERE s.scope_type='PATH'"""
    )
    out={}
    for row in rows:
        payload=json.loads(row["payload_json"] or "{}")
        trigger_state=payload.get("trigger_state") or {}
        current_identity=payload.get("current_state_identity") or {}
        research_status=(
            trigger_state.get("research_status")
            or current_identity.get("research_status")
        )
        last_result_id=(
            trigger_state.get("last_path_result_id")
            or current_identity.get("last_path_result_id")
        )
        out[f'{row["bond_code"]}:{row["scope_id"]}']={
            "bond_code":row["bond_code"],
            "bond_name":row["bond_name"],
            "path_id":row["scope_id"],
            "economic_status":row["economic_status"],
            "economic_path":payload.get("economic_path") or {},
            "research_status":research_status,
            "research_attention":bool(
                (research_status and research_status!="NOT_TRIGGERED")
                or last_result_id
            ),
            "source_snapshot_id":row["source_snapshot_id"],
        }
    return out

def persist_market_transition_changes(
    *, target_db: Path, previous_states: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    conn=connect(target_db)
    detected_at=_now()
    try:
        current_states=_current_market_path_state(conn)
        raw_changes=[]
        for key,current in current_states.items():
            previous=previous_states.get(key)
            previous_status=(
                previous.get("economic_status") if previous else "ABSENT"
            )
            current_status=current.get("economic_status")
            if previous_status==current_status:
                continue
            if current_status not in {"KEEP","DROP"}:
                continue
            change=market_change(
                bond_code=current["bond_code"],
                bond_name=current["bond_name"],
                path_id=current["path_id"],
                snapshot_id=str(current["source_snapshot_id"] or ""),
                previous_economic_status=str(previous_status or "ABSENT"),
                current_economic_status=str(current_status),
                previous_metrics=(previous or {}).get("economic_path") or {},
                current_metrics=current.get("economic_path") or {},
                research_action=(
                    "REUSE_GATE" if current_status=="KEEP" else "NONE"
                ),
                material_metric_signal=False,
                previous_research_status=(previous or {}).get("research_status"),
                previous_research_attention=bool(
                    (previous or {}).get("research_attention")
                ),
            )
            raw_changes.append(attach_notification_decision(change))
        inserted_changes=0
        for change in raw_changes:
            before=conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO change_ledger
                   (change_id,source_type,source_event_update_id,market_snapshot_id,
                    bond_code,scope_type,scope_id,change_type,impact,research_action,
                    notification_level,previous_json,current_json,payload_json,detected_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    change["change_id"],"MARKET",None,change.get("market_snapshot_id"),
                    change["bond_code"],change["scope_type"],change["scope_id"],
                    change["change_type"],change["impact"],change.get("research_action"),
                    change["notification_level"],json_text(change.get("previous_value")),
                    json_text(change.get("current_value")),
                    json_text({
                        "writer_version":MARKET_CHANGE_WRITER_VERSION,
                        "bond_name":change.get("bond_name"),
                        "previous_research_status":change.get(
                            "previous_research_status"
                        ),
                        "previous_research_attention":change.get(
                            "previous_research_attention"
                        ),
                    }),
                    detected_at,
                ),
            )
            inserted_changes += conn.total_changes-before

        groups=coalesce_notification_groups(raw_changes)
        inserted_groups=0
        inserted_links=0
        for group in groups:
            before=conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO notification_group
                   (notification_group_id,group_key,bond_code,level,status,created_at)
                   VALUES (?,?,?,?,?,?)""",
                (
                    group["notification_group_id"],group["group_key"],
                    group["bond_code"],group["level"],"PENDING",detected_at,
                ),
            )
            inserted_groups += conn.total_changes-before
            for change_id in group["change_ids"]:
                before=conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO notification_change_link
                       (notification_group_id,change_id) VALUES (?,?)""",
                    (group["notification_group_id"],change_id),
                )
                inserted_links += conn.total_changes-before
        conn.commit()
        return {
            "market_change_writer_version":MARKET_CHANGE_WRITER_VERSION,
            "status":"PASS",
            "detected_transition_count":len(raw_changes),
            "inserted_change_count":inserted_changes,
            "notification_group_count":len(groups),
            "inserted_notification_group_count":inserted_groups,
            "inserted_notification_link_count":inserted_links,
            "changes":raw_changes,
            "notification_groups":groups,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
