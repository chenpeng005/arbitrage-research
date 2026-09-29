"""Daily information lane: one market-wide notice fetch -> Evidence/Event ledgers.

This module persists deterministic facts and research scheduling intent.
It does not itself execute AI research.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import akshare as ak

from runtime.opportunity.evidence_sources import (
    is_maturity_research_notice,
    is_revision_notice,
    maturity_notice_kind,
    revision_notice_kind,
)
from runtime.opportunity.incremental_change_notification import (
    attach_notification_decision,
    coalesce_notification_groups,
    event_changes,
    semantic_candidate_change,
)
from runtime.opportunity.incremental_event_router import (
    canonicalize_document_set,
    canonicalize_evidence_document,
    route_event,
)
from runtime.opportunity.incremental_research_trigger import (
    plan_event_research_actions,
)
from runtime.opportunity.incremental_storage import connect, json_text

NOTICE_LANE_VERSION = "incremental-notice-lane-v1"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def put_notice_kind(title: str) -> str | None:
    if "回售" not in title:
        return None
    if re.search(r"预计(?:触发|满足).*?回售|回售.*?预计(?:触发|满足)", title):
        return "PUT_EXPECTED_TRIGGER"
    if "回售结果" in title:
        return "PUT_RESULT"
    if re.search(r"(?:已)?触发.*?回售|回售.*?(?:已)?触发", title) and "预计" not in title:
        return "PUT_TRIGGER"
    return None

def classify_notice(title: str) -> str | None:
    put_kind = put_notice_kind(title)
    if put_kind:
        return put_kind
    if is_revision_notice(title):
        kind = revision_notice_kind(title)
        return None if kind == "OTHER" else kind
    if is_maturity_research_notice(title):
        kind = maturity_notice_kind(title)
        return None if kind == "OTHER" else kind
    return None
def _notice_date_text(value: Any) -> str:
    text=str(value or "")
    if " " in text:
        text=text.split(" ",1)[0]
    return text[:10]

def fetch_relevant_daily_notices(
    *,
    notice_date: str,
    target_db: Path,
) -> dict[str, Any]:
    """Fetch all A-share notices once, then map to active convertible bonds."""
    date_compact=notice_date.replace("-","")
    conn=connect(target_db)
    try:
        bond_rows=list(conn.execute(
            """SELECT bond_code,bond_name,stock_code
               FROM bond_master WHERE active=1"""
        ))
    finally:
        conn.close()

    by_stock: dict[str,list[dict[str,str]]] = {}
    for row in bond_rows:
        stock=str(row["stock_code"] or "").zfill(6)
        by_stock.setdefault(stock,[]).append({
            "bond_code":row["bond_code"],
            "bond_name":row["bond_name"],
        })

    try:
        frame=ak.stock_notice_report(symbol="全部",date=date_compact)
    except KeyError as exc:
        # AKShare currently raises KeyError('代码') internally when a requested
        # date has zero notice rows. Treat only this exact boundary as an empty
        # official-notice day; all other source failures remain fail-closed.
        if exc.args == ("代码",):
            return {
                "notice_lane_version":NOTICE_LANE_VERSION,
                "notice_date":notice_date,
                "all_notice_count":0,
                "active_bond_count":len(bond_rows),
                "active_issuer_count":len(by_stock),
                "relevant_notice_count":0,
                "relevant_notices":[],
                "source_empty_day_fallback":True,
            }
        raise
    relevant=[]
    if frame.empty or "代码" not in frame.columns:
        return {
            "notice_lane_version":NOTICE_LANE_VERSION,
            "notice_date":notice_date,
            "all_notice_count":0,
            "active_bond_count":len(bond_rows),
            "active_issuer_count":len(by_stock),
            "relevant_notice_count":0,
            "relevant_notices":[],
        }
    for _,row in frame.iterrows():
        stock=str(row.get("代码") or "").zfill(6)
        bonds=by_stock.get(stock)
        if not bonds:
            continue
        title=str(row.get("公告标题") or "")
        kind=classify_notice(title)
        if not kind:
            continue
        relevant.append({
            "issuer_stock_code":stock,
            "stock_name":str(row.get("名称") or ""),
            "notice_date":_notice_date_text(row.get("公告日期")),
            "title":title,
            "notice_type":str(row.get("公告类型") or ""),
            "url":str(row.get("网址") or ""),
            "event_kind":kind,
            "source":"AKShare/Eastmoney daily official-announcement index",
            "affected_bonds":bonds,
        })

    return {
        "notice_lane_version":NOTICE_LANE_VERSION,
        "notice_date":notice_date,
        "all_notice_count":int(len(frame)),
        "active_bond_count":len(bond_rows),
        "active_issuer_count":len(by_stock),
        "relevant_notice_count":len(relevant),
        "relevant_notices":relevant,
    }

def _event_watermark(prefix: str, ids: list[str]) -> str:
    if not ids:
        return prefix+"_EMPTY"
    raw="|".join(ids)
    return prefix+"_"+hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]

def _economic_statuses(conn, bond_code: str) -> dict[str,str]:
    rows=conn.execute(
        """SELECT scope_id,economic_status FROM scope_state_current
           WHERE bond_code=? AND scope_type='PATH'""",
        (bond_code,),
    )
    out={row["scope_id"]:row["economic_status"] for row in rows}
    out["CREDIT_RISK"]="ACTIVE"
    return out
def _persist_change_rows(conn, changes: list[dict[str,Any]], detected_at: str) -> tuple[int,int,int]:
    inserted_changes=0
    for change in changes:
        before=conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO change_ledger
               (change_id,source_type,source_event_update_id,market_snapshot_id,
                bond_code,scope_type,scope_id,change_type,impact,research_action,
                notification_level,previous_json,current_json,payload_json,detected_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                change["change_id"],change.get("change_source") or "EVENT",
                change.get("source_event_update_id"),change.get("market_snapshot_id"),
                change["bond_code"],change["scope_type"],change["scope_id"],
                change["change_type"],change["impact"],change.get("research_action"),
                change["notification_level"],json_text(change.get("previous_value")),
                json_text(change.get("current_value")),
                json_text({"notice_lane_version":NOTICE_LANE_VERSION,
                           "bond_name":change.get("bond_name")}),
                detected_at,
            ),
        )
        inserted_changes += conn.total_changes-before

    groups=coalesce_notification_groups(changes)
    inserted_groups=inserted_links=0
    for group in groups:
        before=conn.total_changes
        conn.execute(
            """INSERT OR IGNORE INTO notification_group
               (notification_group_id,group_key,bond_code,level,status,created_at)
               VALUES (?,?,?,?,?,?)""",
            (group["notification_group_id"],group["group_key"],group["bond_code"],
             group["level"],"PENDING",detected_at),
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
    return inserted_changes,inserted_groups,inserted_links
def persist_daily_notice_scan(
    *,
    target_db: Path,
    scan: dict[str,Any],
) -> dict[str,Any]:
    """Persist Evidence, Event, routing, changes, notifications and trigger intent."""
    detected_at=_now()
    conn=connect(target_db)
    stats={
        "input_relevant_notices":len(scan.get("relevant_notices") or []),
        "evidence_inserted":0,
        "evidence_bond_links_inserted":0,
        "event_updates_inserted":0,
        "confirmed_events_inserted":0,
        "semantic_candidates_inserted":0,
        "document_only_count":0,
        "event_scope_impacts_inserted":0,
        "research_trigger_rows_inserted":0,
        "change_rows_inserted":0,
        "notification_groups_inserted":0,
        "notification_links_inserted":0,
    }
    try:
        # First persist each Evidence Document once and link it to every affected bond.
        docs_by_bond: dict[str,dict[str,Any]] = {}
        for notice in scan.get("relevant_notices") or []:
            bonds=notice.get("affected_bonds") or []
            if not bonds:
                continue
            probe=canonicalize_evidence_document(
                bond_code=bonds[0]["bond_code"],
                bond_name=bonds[0]["bond_name"],
                document=notice,
            )
            evidence_id=probe["evidence_id"]
            before=conn.total_changes
            conn.execute(
                """INSERT OR IGNORE INTO evidence_document
                   (evidence_id,issuer_stock_code,published_at,title,source_kind,url,
                    content_sha256,artifact_path,metadata_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    evidence_id,notice.get("issuer_stock_code"),notice.get("notice_date"),
                    notice.get("title"),notice.get("event_kind"),notice.get("url"),
                    None,None,json_text({
                        "notice_type":notice.get("notice_type"),
                        "stock_name":notice.get("stock_name"),
                        "source":notice.get("source"),
                    }),detected_at,
                ),
            )
            stats["evidence_inserted"] += conn.total_changes-before
            for bond in bonds:
                before=conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO evidence_bond_link
                       (evidence_id,bond_code,link_role) VALUES (?,?,?)""",
                    (evidence_id,bond["bond_code"],"AFFECTS"),
                )
                stats["evidence_bond_links_inserted"] += conn.total_changes-before
                key=bond["bond_code"]
                entry=docs_by_bond.setdefault(key,{
                    "bond_name":bond["bond_name"],"documents":[]
                })
                entry["documents"].append(notice)
        # Canonicalize per bond so one issuer notice can produce N bond Events.
        for bond_code,entry in sorted(docs_by_bond.items()):
            canonical=canonicalize_document_set(
                bond_code=bond_code,
                bond_name=entry["bond_name"],
                documents=entry["documents"],
            )
            stats["document_only_count"] += len(canonical.get("document_only") or [])

            # Document-only semantic audits (e.g. trustee report) are queued by Evidence.
            for doc in canonical.get("document_only") or []:
                if not doc.get("requires_semantic_audit"):
                    continue
                trigger_key=f'EVIDENCE:{doc["evidence_id"]}:{bond_code}:SEMANTIC_AUDIT'
                before=conn.total_changes
                conn.execute(
                    """INSERT OR IGNORE INTO research_trigger_ledger
                       (trigger_key,bond_code,path_id,source_event_update_id,
                        research_action,task_kind,status,task_id,emitted_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        trigger_key,bond_code,None,None,"SEMANTIC_AUDIT",
                        "EVENT_SEMANTIC_AUDIT","PENDING",None,detected_at,detected_at,
                    ),
                )
                stats["research_trigger_rows_inserted"] += conn.total_changes-before

            for family in canonical.get("event_families") or []:
                family_id=family["event_family_id"]
                conn.execute(
                    """INSERT OR IGNORE INTO event_family
                       (event_family_id,bond_code,event_family,latest_confirmed_version,
                        confirmed_watermark,semantic_candidate_watermark,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (family_id,bond_code,family["event_family"],0,
                     "EWM_EMPTY","ECM_EMPTY",detected_at),
                )

                candidates=[
                    ("CONFIRMED",u) for u in family.get("updates") or []
                ] + [
                    ("SEMANTIC_CANDIDATE",u)
                    for u in family.get("semantic_update_candidates") or []
                ]
                for confirmation_status,update in candidates:
                    event_id=update["event_update_id"]
                    existing=conn.execute(
                        "SELECT 1 FROM event_update WHERE event_update_id=?",
                        (event_id,),
                    ).fetchone()
                    is_new=existing is None
                    if is_new:
                        if confirmation_status=="CONFIRMED":
                            next_version=conn.execute(
                                """SELECT COALESCE(MAX(event_version),0)+1 n
                                   FROM event_update WHERE event_family_id=?
                                   AND confirmation_status='CONFIRMED'""",
                                (family_id,),
                            ).fetchone()["n"]
                            event_version=int(next_version); candidate_version=None
                        else:
                            next_version=conn.execute(
                                """SELECT COALESCE(MAX(candidate_version),0)+1 n
                                   FROM event_update WHERE event_family_id=?
                                   AND confirmation_status='SEMANTIC_CANDIDATE'""",
                                (family_id,),
                            ).fetchone()["n"]
                            event_version=None; candidate_version=int(next_version)
                        conn.execute(
                            """INSERT INTO event_update
                               (event_update_id,event_family_id,confirmation_status,
                                event_version,candidate_version,occurred_at,
                                materiality_status,payload_json,created_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (
                                event_id,family_id,confirmation_status,
                                event_version,candidate_version,update.get("notice_date"),
                                "CONFIRMED" if confirmation_status=="CONFIRMED" else "UNKNOWN",
                                json_text({
                                    "notice_lane_version":NOTICE_LANE_VERSION,
                                    "canonicalization_status":update.get("canonicalization_status"),
                                }),detected_at,
                            ),
                        )
                        stats["event_updates_inserted"] += 1
                        if confirmation_status=="CONFIRMED":
                            stats["confirmed_events_inserted"] += 1
                        else:
                            stats["semantic_candidates_inserted"] += 1

                    for evidence_id in update.get("supporting_evidence_ids") or []:
                        conn.execute(
                            """INSERT OR IGNORE INTO event_evidence_link
                               (event_update_id,evidence_id,link_role)
                               VALUES (?,?,?)""",
                            (event_id,evidence_id,"SUPPORTING"),
                        )

                    if not is_new:
                        continue
                    event={
                        "event_update_id":event_id,
                        "event_family":family["event_family"],
                        "requires_semantic_audit":confirmation_status!="CONFIRMED",
                    }
                    if confirmation_status=="CONFIRMED":
                        routing=route_event(event)
                        for route in routing.get("routes") or []:
                            scope=route["scope"]
                            scope_type="RISK" if scope.endswith("_RISK") else "PATH"
                            before=conn.total_changes
                            conn.execute(
                                """INSERT OR IGNORE INTO event_scope_impact
                                   (event_update_id,scope_type,scope_id,impact,
                                    research_action,notification_hint,reason,route_version)
                                   VALUES (?,?,?,?,?,?,?,?)""",
                                (
                                    event_id,scope_type,scope,route["impact"],
                                    route["research_action"],route["notification"],
                                    route["reason"],"incremental-event-router-v2-token-gate",
                                ),
                            )
                            stats["event_scope_impacts_inserted"] += conn.total_changes-before

                        existing_keys={
                            row["trigger_key"] for row in conn.execute(
                                "SELECT trigger_key FROM research_trigger_ledger"
                            )
                        }
                        plan=plan_event_research_actions(
                            event=event,
                            routing=routing,
                            economic_status_by_scope=_economic_statuses(conn,bond_code),
                            previously_emitted_keys=existing_keys,
                        )
                        for action in plan.get("all_actions") or []:
                            before=conn.total_changes
                            conn.execute(
                                """INSERT OR IGNORE INTO research_trigger_ledger
                                   (trigger_key,bond_code,path_id,source_event_update_id,
                                    research_action,task_kind,status,task_id,
                                    emitted_at,updated_at)
                                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                                (
                                    action["trigger_key"],bond_code,action["scope"],event_id,
                                    action["research_action"],action["task_kind"],
                                    action["task_status"],None,detected_at,detected_at,
                                ),
                            )
                            stats["research_trigger_rows_inserted"] += conn.total_changes-before

                        changes=[
                            attach_notification_decision(x)
                            for x in event_changes(
                                bond_code=bond_code,bond_name=entry["bond_name"],
                                event=event,routing=routing,
                            )
                        ]
                    else:
                        trigger_key=f'SEMANTIC:{event_id}:BOND'
                        before=conn.total_changes
                        conn.execute(
                            """INSERT OR IGNORE INTO research_trigger_ledger
                               (trigger_key,bond_code,path_id,source_event_update_id,
                                research_action,task_kind,status,task_id,
                                emitted_at,updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (
                                trigger_key,bond_code,None,event_id,"SEMANTIC_AUDIT",
                                "EVENT_SEMANTIC_AUDIT","PENDING",None,
                                detected_at,detected_at,
                            ),
                        )
                        stats["research_trigger_rows_inserted"] += conn.total_changes-before
                        changes=[attach_notification_decision(
                            semantic_candidate_change(
                                bond_code=bond_code,bond_name=entry["bond_name"],
                                event_candidate=event,
                            )
                        )]

                    a,b,c=_persist_change_rows(conn,changes,detected_at)
                    stats["change_rows_inserted"] += a
                    stats["notification_groups_inserted"] += b
                    stats["notification_links_inserted"] += c

                # Recompute stable family watermarks from persisted Update IDs.
                confirmed=[
                    row["event_update_id"] for row in conn.execute(
                        """SELECT event_update_id FROM event_update
                           WHERE event_family_id=? AND confirmation_status='CONFIRMED'
                           ORDER BY event_version""",(family_id,)
                    )
                ]
                semantic=[
                    row["event_update_id"] for row in conn.execute(
                        """SELECT event_update_id FROM event_update
                           WHERE event_family_id=? AND confirmation_status='SEMANTIC_CANDIDATE'
                           ORDER BY candidate_version""",(family_id,)
                    )
                ]
                conn.execute(
                    """UPDATE event_family SET latest_confirmed_version=?,
                       confirmed_watermark=?,semantic_candidate_watermark=?,updated_at=?
                       WHERE event_family_id=?""",
                    (
                        len(confirmed),_event_watermark("EWM",confirmed),
                        _event_watermark("ECM",semantic),detected_at,family_id,
                    ),
                )

        conn.commit()
        return {
            "notice_lane_version":NOTICE_LANE_VERSION,
            "status":"PASS",
            "notice_date":scan.get("notice_date"),
            "all_notice_count":scan.get("all_notice_count"),
            "relevant_notice_count":scan.get("relevant_notice_count"),
            "stats":stats,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def run_daily_notice_lane(*, notice_date: str, target_db: Path) -> dict[str,Any]:
    scan=fetch_relevant_daily_notices(notice_date=notice_date,target_db=target_db)
    persisted=persist_daily_notice_scan(target_db=target_db,scan=scan)
    return {"scan":scan,"persisted":persisted}


def run_daily_notice_lane_job(
    *,
    notice_date: str,
    data_root: Path,
    target_db: Path | None = None,
) -> dict[str,Any]:
    """Run one formal daily notice job and persist immutable run artifacts."""
    target_db=target_db or (data_root/"state"/"incremental_runtime.sqlite")
    run_id=(
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        +"_notice_lane_"+uuid.uuid4().hex[:8]
    )
    run_dir=data_root/"runs"/run_id
    run_dir.mkdir(parents=True,exist_ok=False)

    result=run_daily_notice_lane(
        notice_date=notice_date,
        target_db=target_db,
    )
    scan_path=run_dir/"daily_notice_scan.json"
    result_path=run_dir/"notice_lane_result.json"
    scan_path.write_text(
        json.dumps(result["scan"],ensure_ascii=False,indent=2)+"\n",
        encoding="utf-8",
    )
    payload={
        "notice_lane_version":NOTICE_LANE_VERSION,
        "run_id":run_id,
        "unit":"DAILY_NOTICE_LANE",
        "status":result["persisted"]["status"],
        "notice_date":notice_date,
        "database_path":str(target_db.resolve()),
        "scan_path":str(scan_path.resolve()),
        "persisted":result["persisted"],
        "completed_at":_now(),
    }
    result_path.write_text(
        json.dumps(payload,ensure_ascii=False,indent=2)+"\n",
        encoding="utf-8",
    )
    latest={
        "notice_lane_version":NOTICE_LANE_VERSION,
        "run_id":run_id,
        "status":payload["status"],
        "notice_date":notice_date,
        "all_notice_count":result["scan"]["all_notice_count"],
        "relevant_notice_count":result["scan"]["relevant_notice_count"],
        "result_path":str(result_path.resolve()),
        "scan_path":str(scan_path.resolve()),
        "completed_at":payload["completed_at"],
    }
    latest_path=data_root/"registry"/"latest_notice_lane.json"
    latest_path.write_text(
        json.dumps(latest,ensure_ascii=False,indent=2)+"\n",
        encoding="utf-8",
    )
    return {**payload,"latest_pointer_path":str(latest_path.resolve())}

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]