"""Persist daily Evidence → Event → Route → Trigger → Change → Notification."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_change_notification import (
    attach_notification_decision,
    coalesce_notification_groups,
    event_changes,
    semantic_candidate_change,
)
from runtime.opportunity.incremental_event_router import (
    EVENT_ROUTER_VERSION,
    canonicalize_document_set,
    canonicalize_evidence_document,
    route_event,
)
from runtime.opportunity.incremental_research_trigger import (
    plan_event_research_actions,
)
from runtime.opportunity.incremental_storage import connect, json_text

EVENT_LEDGER_WRITER_VERSION = "incremental-event-ledger-writer-v1"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _watermark(prefix: str, ids: list[str]) -> str:
    if not ids:
        return prefix + "_EMPTY"
    raw = "|".join(ids)
    return prefix + "_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

def _economic_status_by_scope(conn, bond_code: str) -> dict[str, str]:
    rows=conn.execute(
        """SELECT scope_id,economic_status FROM scope_state_current
           WHERE bond_code=? AND scope_type='PATH'""",
        (bond_code,),
    )
    return {str(x["scope_id"]):str(x["economic_status"] or "") for x in rows}

def _emitted_trigger_keys(conn) -> set[str]:
    return {
        str(x["trigger_key"])
        for x in conn.execute("SELECT trigger_key FROM research_trigger_ledger")
    }
def _ensure_event_family(
    conn, *, family_id: str, bond_code: str, event_family: str, now: str
) -> None:
    conn.execute(
        """INSERT OR IGNORE INTO event_family
           (event_family_id,bond_code,event_family,latest_confirmed_version,
            confirmed_watermark,semantic_candidate_watermark,updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (family_id,bond_code,event_family,0,"EWM_EMPTY","ECM_EMPTY",now),
    )

def _refresh_family_watermarks(conn, family_id: str, now: str) -> None:
    confirmed=[
        str(x["event_update_id"])
        for x in conn.execute(
            """SELECT event_update_id FROM event_update
               WHERE event_family_id=? AND confirmation_status='CONFIRMED'
               ORDER BY event_version""",
            (family_id,),
        )
    ]
    candidates=[
        str(x["event_update_id"])
        for x in conn.execute(
            """SELECT event_update_id FROM event_update
               WHERE event_family_id=? AND confirmation_status='SEMANTIC_CANDIDATE'
               ORDER BY candidate_version""",
            (family_id,),
        )
    ]
    conn.execute(
        """UPDATE event_family SET latest_confirmed_version=?,
           confirmed_watermark=?,semantic_candidate_watermark=?,updated_at=?
           WHERE event_family_id=?""",
        (
            len(confirmed),_watermark("EWM",confirmed),
            _watermark("ECM",candidates),now,family_id,
        ),
    )

def _next_version(conn, family_id: str, column: str) -> int:
    if column not in {"event_version","candidate_version"}:
        raise ValueError(column)
    row=conn.execute(
        f"SELECT COALESCE(MAX({column}),0) n FROM event_update WHERE event_family_id=?",
        (family_id,),
    ).fetchone()
    return int(row["n"])+1
def _insert_change(conn, raw: dict[str, Any], now: str) -> int:
    change=attach_notification_decision(raw)
    before=conn.total_changes
    conn.execute(
        """INSERT OR IGNORE INTO change_ledger
           (change_id,source_type,source_event_update_id,market_snapshot_id,
            bond_code,scope_type,scope_id,change_type,impact,research_action,
            notification_level,previous_json,current_json,payload_json,detected_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            change["change_id"],change["change_source"],
            change.get("source_event_update_id"),change.get("market_snapshot_id"),
            change["bond_code"],change["scope_type"],change["scope_id"],
            change["change_type"],change["impact"],change.get("research_action"),
            change["notification_level"],json_text(change.get("previous_value")),
            json_text(change.get("current_value")),
            json_text({
                "writer_version":EVENT_LEDGER_WRITER_VERSION,
                "bond_name":change.get("bond_name"),
                "event_family":change.get("event_family"),
                "route_reason":change.get("route_reason"),
            }),
            now,
        ),
    )
    return conn.total_changes-before

def _persist_notification_groups(conn, changes: list[dict[str, Any]], now: str) -> tuple[int,int]:
    groups=coalesce_notification_groups(changes)
    inserted_groups=inserted_links=0
    for group in groups:
        before=conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO notification_group
               (notification_group_id,group_key,bond_code,level,status,created_at)
               VALUES (?,?,?,?,?,?)""",
            (
                group["notification_group_id"],group["group_key"],
                group["bond_code"],group["level"],"PENDING",now,
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
    return inserted_groups, inserted_links
def persist_information_scan(
    *, target_db: Path, scan: dict[str, Any]
) -> dict[str, Any]:
    if scan.get("status")!="PASS":
        raise ValueError("information scan is not PASS")
    conn=connect(target_db)
    now=_now()
    stats={
        "evidence_documents_inserted":0,"evidence_bond_links_inserted":0,
        "confirmed_events_inserted":0,"semantic_candidates_inserted":0,
        "event_evidence_links_inserted":0,"event_scope_impacts_inserted":0,
        "research_triggers_inserted":0,"changes_inserted":0,
        "notification_groups_inserted":0,"notification_links_inserted":0,
    }
    all_changes: list[dict[str, Any]]=[]
    docs_by_bond: dict[str, dict[str, Any]]={}

    try:
        # Physical Evidence is stored once, then linked to every affected bond.
        for doc in scan.get("documents",[]):
            affected=doc.get("affected_bonds") or []
            if not affected:
                continue
            first=affected[0]
            canonical=canonicalize_evidence_document(
                bond_code=first["bond_code"],bond_name=first["bond_name"],document=doc
            )
            evidence_id=str(canonical["evidence_id"])
            before=conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO evidence_document
                   (evidence_id,issuer_stock_code,published_at,title,source_kind,url,
                    content_sha256,artifact_path,metadata_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,doc.get("issuer_stock_code"),doc.get("notice_date"),
                    doc.get("title") or "","EASTMONEY_NOTICE_INDEX",doc.get("url"),
                    None,None,json_text({
                        "notice_type":doc.get("notice_type"),
                        "issuer_name":doc.get("issuer_name"),
                        "event_kind":doc.get("event_kind"),
                        "source":doc.get("source"),
                        "scan_date":scan.get("scan_date"),
                    }),now,
                ),
            )
            stats["evidence_documents_inserted"] += conn.total_changes-before
            for bond in affected:
                code=str(bond["bond_code"]).zfill(6)
                before=conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO evidence_bond_link
                       (evidence_id,bond_code,link_role) VALUES (?,?,?)""",
                    (evidence_id,code,"AFFECTS"),
                )
                stats["evidence_bond_links_inserted"] += conn.total_changes-before
                bucket=docs_by_bond.setdefault(code,{
                    "bond_name":bond["bond_name"],"documents":[]
                })
                bucket["documents"].append(doc)
        emitted=_emitted_trigger_keys(conn)
        for bond_code,bucket in sorted(docs_by_bond.items()):
            canonical_set=canonicalize_document_set(
                bond_code=bond_code,
                bond_name=bucket["bond_name"],
                documents=bucket["documents"],
            )

            # Document-only evidence (for example trustee reports) may repeat
            # old facts. Keep the document, but require a lightweight semantic
            # audit before creating any business Event.
            for doc in canonical_set.get("document_only", []):
                if not doc.get("requires_semantic_audit"):
                    continue
                evidence_id=str(doc["evidence_id"])
                trigger_key=(
                    f"EVIDENCE:{evidence_id}:{bond_code}:SEMANTIC_AUDIT"
                )
                before=conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO research_trigger_ledger
                       (trigger_key,bond_code,path_id,source_event_update_id,
                        research_action,task_kind,status,task_id,emitted_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trigger_key,bond_code,None,None,"SEMANTIC_AUDIT",
                        "EVENT_SEMANTIC_AUDIT","PENDING",None,now,now,
                    ),
                )
                stats["research_triggers_inserted"] += conn.total_changes-before
                emitted.add(trigger_key)

            for family in canonical_set.get("event_families",[]):
                family_id=str(family["event_family_id"])
                family_name=str(family["event_family"])
                _ensure_event_family(
                    conn,family_id=family_id,bond_code=bond_code,
                    event_family=family_name,now=now,
                )

                for status_key,confirmation,version_col,stat_key in [
                    ("updates","CONFIRMED","event_version","confirmed_events_inserted"),
                    ("semantic_update_candidates","SEMANTIC_CANDIDATE","candidate_version","semantic_candidates_inserted"),
                ]:
                    for update in family.get(status_key,[]):
                        update_id=str(update["event_update_id"])
                        exists=conn.execute(
                            "SELECT confirmation_status FROM event_update WHERE event_update_id=?",
                            (update_id,),
                        ).fetchone()
                        inserted=False
                        if exists is None:
                            version=_next_version(conn,family_id,version_col)
                            event_version=version if confirmation=="CONFIRMED" else None
                            candidate_version=version if confirmation=="SEMANTIC_CANDIDATE" else None
                            conn.execute(
                                """INSERT INTO event_update
                                   (event_update_id,event_family_id,confirmation_status,
                                    event_version,candidate_version,occurred_at,
                                    materiality_status,payload_json,created_at)
                                   VALUES (?,?,?,?,?,?,?,?,?)""",
                                (
                                    update_id,family_id,confirmation,event_version,
                                    candidate_version,update.get("notice_date"),
                                    "UNKNOWN" if confirmation=="SEMANTIC_CANDIDATE" else "ROUTABLE",
                                    json_text(update),now,
                                ),
                            )
                            stats[stat_key]+=1
                            inserted=True

                        for evidence_id in update.get("supporting_evidence_ids",[]):
                            before=conn.total_changes
                            conn.execute(
                                """INSERT OR IGNORE INTO event_evidence_link
                                   (event_update_id,evidence_id,link_role) VALUES (?,?,?)""",
                                (update_id,evidence_id,"SUPPORTING"),
                            )
                            stats["event_evidence_links_inserted"] += conn.total_changes-before

                        if not inserted:
                            continue
                        event_obj={
                            **update,
                            "event_update_id":update_id,
                            "event_family":family_name,
                            "requires_semantic_audit":confirmation!="CONFIRMED",
                        }
                        if confirmation=="SEMANTIC_CANDIDATE":
                            trigger_key=f"EVENT:{update_id}:EVENT_AUDIT:SEMANTIC_AUDIT"
                            before=conn.total_changes
                            conn.execute(
                                """INSERT OR IGNORE INTO research_trigger_ledger
                                   (trigger_key,bond_code,path_id,source_event_update_id,
                                    research_action,task_kind,status,task_id,emitted_at,updated_at)
                                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    trigger_key,bond_code,None,update_id,"SEMANTIC_AUDIT",
                                    "EVENT_SEMANTIC_AUDIT","PENDING",None,now,now,
                                ),
                            )
                            stats["research_triggers_inserted"] += conn.total_changes-before
                            emitted.add(trigger_key)
                            all_changes.append(
                                semantic_candidate_change(
                                    bond_code=bond_code,bond_name=bucket["bond_name"],
                                    event_candidate=event_obj,
                                )
                            )
                            continue

                        routing=route_event(event_obj)
                        for route in routing.get("routes",[]):
                            scope=str(route.get("scope") or "")
                            scope_type="RISK" if scope.endswith("_RISK") else "PATH"
                            before=conn.total_changes
                            conn.execute(
                                """INSERT OR IGNORE INTO event_scope_impact
                                   (event_update_id,scope_type,scope_id,impact,research_action,
                                    notification_hint,reason,route_version)
                                   VALUES (?,?,?,?,?,?,?,?)""",
                                (
                                    update_id,scope_type,scope,route.get("impact"),
                                    route.get("research_action"),route.get("notification"),
                                    route.get("reason"),EVENT_ROUTER_VERSION,
                                ),
                            )
                            stats["event_scope_impacts_inserted"] += conn.total_changes-before
                        planned=plan_event_research_actions(
                            event=event_obj,routing=routing,
                            economic_status_by_scope=_economic_status_by_scope(conn,bond_code),
                            previously_emitted_keys=emitted,
                        )
                        for action in planned.get("new_actions",[]):
                            before=conn.total_changes
                            conn.execute(
                                """INSERT OR IGNORE INTO research_trigger_ledger
                                   (trigger_key,bond_code,path_id,source_event_update_id,
                                    research_action,task_kind,status,task_id,emitted_at,updated_at)
                                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    action["trigger_key"],bond_code,action.get("scope"),
                                    update_id,action.get("research_action"),
                                    action.get("task_kind"),action.get("task_status"),
                                    None,now,now,
                                ),
                            )
                            stats["research_triggers_inserted"] += conn.total_changes-before
                            emitted.add(str(action["trigger_key"]))
                        all_changes.extend(
                            event_changes(
                                bond_code=bond_code,bond_name=bucket["bond_name"],
                                event=event_obj,routing=routing,
                            )
                        )

                _refresh_family_watermarks(conn,family_id,now)

        for change in all_changes:
            stats["changes_inserted"] += _insert_change(conn,change,now)
        g,l=_persist_notification_groups(conn,all_changes,now)
        stats["notification_groups_inserted"]+=g
        stats["notification_links_inserted"]+=l
        conn.commit()
        return {
            "event_ledger_writer_version":EVENT_LEDGER_WRITER_VERSION,
            "status":"PASS","scan_date":scan.get("scan_date"),
            "input_relevant_document_count":scan.get("relevant_document_count",0),
            "stats":stats,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]