"""Production bridge for Research Reuse and Event-watermark validity."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_information_scan import scan_daily_relevant_notices
from runtime.opportunity.incremental_storage import connect, json_text
from runtime.opportunity.research_reuse import (
    FULL_V2_RESEARCH,
    REUSE_PREVIOUS,
    SEMANTIC_AUDIT,
    evaluate_research_reuse,
)

REUSE_RUNTIME_VERSION = "incremental-research-reuse-runtime-v1"
KEEP_ENTRY_REASONS = {"MATURITY_KEEP_ENTRY", "PUT_KEEP_ENTRY"}
TASK_CONTRACT_CANONICAL = (
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/Path-Research/"
    "Path-Research-Task-Contract-V2.md"
)
EVIDENCE_CONTRACT_CANONICAL = (
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/Path-Research/"
    "Path-Research-Evidence-Pack-V2.md"
)
RESULT_CONTRACT_CANONICAL = (
    "05 套利研究/AI-Engineering-Runtime/03_节点设计/Path-Research/"
    "Path-Research-Result-Contract-V2.md"
)
BACKWARD_COMPATIBLE_RESULT_CONTRACT_HASH_PAIRS = {
    (
        "491e47452a12e3f80921a3549bdd70e7a5855eac3a3041a3d5a54611baa80e2a",
        "62a64563a3bfcb243c6e53bacfae59cf1d84148db82c1b273c216c82b7123a66",
    ): "DOWNWARD_REVISION x/N validator false-positive fix; no output-contract tightening",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stable_hash(value: Any, prefix: str) -> str:
    raw=json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"))
    return prefix+"_"+hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def bond_event_watermarks(
    conn,
    bond_code: str,
    *,
    cutoff: str | None = None,
) -> dict[str, str]:
    params: list[Any]=[str(bond_code).zfill(6)]
    cutoff_sql=""
    if cutoff:
        cutoff_sql=" AND COALESCE(eu.occurred_at,'')<=?"
        params.append(str(cutoff)[:10])
    rows=list(conn.execute(
        f"""SELECT ef.event_family,eu.event_update_id,eu.confirmation_status,
                   eu.occurred_at,eu.materiality_status
            FROM event_update eu
            JOIN event_family ef ON ef.event_family_id=eu.event_family_id
            WHERE ef.bond_code=?
              AND eu.confirmation_status IN ('CONFIRMED','SEMANTIC_CANDIDATE')
              {cutoff_sql}
            ORDER BY ef.event_family,eu.occurred_at,eu.event_update_id""",
        tuple(params),
    ))
    confirmed=[
        {
            "event_family":r["event_family"],
            "event_update_id":r["event_update_id"],
            "occurred_at":r["occurred_at"],
            "materiality_status":r["materiality_status"],
        }
        for r in rows if r["confirmation_status"]=="CONFIRMED"
    ]
    candidates=[
        {
            "event_family":r["event_family"],
            "event_update_id":r["event_update_id"],
            "occurred_at":r["occurred_at"],
        }
        for r in rows if r["confirmation_status"]=="SEMANTIC_CANDIDATE"
    ]
    return {
        "confirmed":_stable_hash(confirmed,"BEW"),
        "candidate":_stable_hash(candidates,"BCW"),
    }


def advance_current_binding_watermarks(
    conn,
    *,
    bond_code: str,
    path_id: str,
    reason: str,
    source_event_update_id: str | None = None,
    updated_at: str | None = None,
) -> dict[str, Any]:
    updated_at=updated_at or _now()
    binding=conn.execute(
        """SELECT binding_id,validity_basis_json FROM research_binding
           WHERE bond_code=? AND path_id=? AND is_current=1""",
        (str(bond_code).zfill(6),path_id),
    ).fetchone()
    if binding is None:
        return {"status":"SKIPPED","reason":"NO_CURRENT_BINDING"}
    state=conn.execute(
        """SELECT state_version,research_state_version FROM scope_state_current
           WHERE bond_code=? AND scope_type='PATH' AND scope_id=?""",
        (str(bond_code).zfill(6),path_id),
    ).fetchone()
    marks=bond_event_watermarks(conn,str(bond_code).zfill(6))
    basis=json.loads(binding["validity_basis_json"] or "{}")
    basis.update({
        "event_watermark_status":"ESTABLISHED",
        "event_watermark_advanced_at":updated_at,
        "event_watermark_advance_reason":reason,
        "source_event_update_id":source_event_update_id,
        "reuse_runtime_version":REUSE_RUNTIME_VERSION,
    })
    conn.execute(
        """UPDATE research_binding
           SET checked_event_watermark_hash=?,
               checked_candidate_watermark_hash=?,
               checked_state_version=?,
               checked_research_state_version=?,
               validity_basis_json=?
           WHERE binding_id=?""",
        (
            marks["confirmed"],marks["candidate"],
            state["state_version"] if state else None,
            state["research_state_version"] if state else None,
            json_text(basis),binding["binding_id"],
        ),
    )
    return {
        "status":"PASS",
        "binding_id":binding["binding_id"],
        "confirmed_watermark":marks["confirmed"],
        "candidate_watermark":marks["candidate"],
    }
def _date_range(start_exclusive: str, end_exclusive: str) -> list[str]:
    start=datetime.strptime(str(start_exclusive)[:10],"%Y-%m-%d").date()+timedelta(days=1)
    end=datetime.strptime(str(end_exclusive)[:10],"%Y-%m-%d").date()
    out=[]
    while start<end:
        out.append(start.strftime("%Y%m%d"))
        start+=timedelta(days=1)
    return out


def bootstrap_pre_event_binding_watermarks(
    *,
    data_root: Path,
    target_db: Path,
    activation_date: str,
) -> dict[str, Any]:
    """Establish a checkpoint only when the pre-ledger gap has zero relevant docs."""
    conn=connect(target_db)
    rows=list(conn.execute(
        """SELECT rb.binding_id,rb.bond_code,rb.path_id,rb.validity_status,
                  rb.checked_event_watermark_hash,rb.validity_basis_json,
                  rr.result_id,rr.research_cutoff
           FROM research_binding rb
           JOIN research_result_index rr ON rr.result_id=rb.result_id
           WHERE rb.is_current=1 AND rb.validity_status='VALID'
           ORDER BY rb.bond_code,rb.path_id"""
    ))
    conn.close()

    scan_dates=sorted({
        d
        for row in rows
        for d in _date_range(str(row["research_cutoff"]),activation_date)
    })
    scans={}
    for compact in scan_dates:
        scans[compact]=scan_daily_relevant_notices(
            target_db=target_db,date=compact
        )

    docs_by_bond: dict[str,list[dict[str,Any]]]={}
    for compact,scan in scans.items():
        for doc in scan.get("documents",[]):
            for bond in doc.get("affected_bonds") or []:
                code=str(bond["bond_code"]).zfill(6)
                docs_by_bond.setdefault(code,[]).append({
                    "scan_date":compact,
                    "notice_date":doc.get("notice_date"),
                    "event_kind":doc.get("event_kind"),
                    "title":doc.get("title"),
                    "url":doc.get("url"),
                })

    now=_now()
    conn=connect(target_db)
    established=[]
    unresolved=[]
    try:
        for row in rows:
            code=str(row["bond_code"]).zfill(6)
            cutoff=str(row["research_cutoff"])[:10]
            relevant=[
                d for d in docs_by_bond.get(code,[])
                if cutoff < str(d.get("notice_date") or "")[:10] < activation_date
            ]
            basis=json.loads(row["validity_basis_json"] or "{}")
            if relevant:
                basis.update({
                    "watermark_bootstrap_status":"UNRESOLVED_RELEVANT_EVIDENCE",
                    "activation_date":activation_date,
                    "gap_relevant_evidence_count":len(relevant),
                    "gap_relevant_evidence":relevant,
                    "reuse_runtime_version":REUSE_RUNTIME_VERSION,
                    "watermark_bootstrap_checked_at":now,
                })
                conn.execute(
                    """UPDATE research_binding
                       SET validity_status='UPDATE_PENDING',
                           validity_basis_json=?
                       WHERE binding_id=?""",
                    (json_text(basis),row["binding_id"]),
                )
                unresolved.append({
                    "binding_id":row["binding_id"],"bond_code":code,
                    "path_id":row["path_id"],"research_cutoff":cutoff,
                    "relevant_evidence_count":len(relevant),
                    "evidence":relevant,
                })
                continue

            marks=bond_event_watermarks(conn,code)
            state=conn.execute(
                """SELECT state_version,research_state_version
                   FROM scope_state_current
                   WHERE bond_code=? AND scope_type='PATH' AND scope_id=?""",
                (code,row["path_id"]),
            ).fetchone()
            basis.update({
                "event_watermark_status":"ESTABLISHED",
                "watermark_bootstrap_status":"ESTABLISHED_NO_RELEVANT_EVIDENCE",
                "activation_date":activation_date,
                "gap_scan_dates":_date_range(cutoff,activation_date),
                "gap_relevant_evidence_count":0,
                "reuse_runtime_version":REUSE_RUNTIME_VERSION,
                "watermark_bootstrap_checked_at":now,
            })
            conn.execute(
                """UPDATE research_binding
                   SET checked_event_watermark_hash=?,
                       checked_candidate_watermark_hash=?,
                       checked_state_version=?,
                       checked_research_state_version=?,
                       validity_basis_json=?
                   WHERE binding_id=?""",
                (
                    marks["confirmed"],marks["candidate"],
                    state["state_version"] if state else None,
                    state["research_state_version"] if state else None,
                    json_text(basis),row["binding_id"],
                ),
            )
            established.append({
                "binding_id":row["binding_id"],"bond_code":code,
                "path_id":row["path_id"],"research_cutoff":cutoff,
                "confirmed_watermark":marks["confirmed"],
                "candidate_watermark":marks["candidate"],
            })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    result={
        "reuse_runtime_version":REUSE_RUNTIME_VERSION,
        "status":"PASS",
        "activation_date":activation_date,
        "scan_dates":scan_dates,
        "scans":{
            d:{
                "all_notice_count":s.get("all_notice_count"),
                "relevant_document_count":s.get("relevant_document_count"),
            } for d,s in scans.items()
        },
        "binding_count":len(rows),
        "established_count":len(established),
        "unresolved_count":len(unresolved),
        "established":established,
        "unresolved":unresolved,
        "completed_at":now,
    }
    _write_json(data_root/"state"/"research_watermark_bootstrap.json",result)
    return result
def _snapshot_manifest(data_root: Path, sha: str) -> dict[str,Any] | None:
    path=data_root/"knowledge_snapshots"/sha/"manifest.json"
    return _read_json(path) if path.exists() else None


def _canonical_compatible(
    *,
    data_root: Path,
    previous_task: dict[str,Any],
    current_task: dict[str,Any],
) -> bool | None:
    old_sha=str(previous_task.get("knowledge_commit_sha") or "")
    new_sha=str(current_task.get("knowledge_commit_sha") or "")
    if not old_sha or not new_sha:
        return None
    old=_snapshot_manifest(data_root,old_sha)
    new=_snapshot_manifest(data_root,new_sha)
    if old is None or new is None:
        return None
    old_map={x["canonical_path"]:x["sha256"] for x in old.get("files",[])}
    new_map={x["canonical_path"]:x["sha256"] for x in new.get("files",[])}
    path_canonical=str(current_task.get("path_research_canonical_path") or "")
    exact_paths=(
        path_canonical,
        TASK_CONTRACT_CANONICAL,
        EVIDENCE_CONTRACT_CANONICAL,
    )
    if any(not p or p not in old_map or p not in new_map for p in exact_paths):
        return None
    if not all(old_map[p]==new_map[p] for p in exact_paths):
        return False
    if RESULT_CONTRACT_CANONICAL not in old_map or RESULT_CONTRACT_CANONICAL not in new_map:
        return None
    old_result=old_map[RESULT_CONTRACT_CANONICAL]
    new_result=new_map[RESULT_CONTRACT_CANONICAL]
    if old_result==new_result:
        return True
    return (
        old_result,
        new_result,
    ) in BACKWARD_COMPATIBLE_RESULT_CONTRACT_HASH_PAIRS


def _binding_row(conn,bond_code: str,path_id: str):
    return conn.execute(
        """SELECT rb.*,rr.task_id result_task_id,rr.trigger_key result_trigger_key,
                  rr.artifact_path,rr.review_ready,rr.research_status,
                  rr.canonical_commit_sha
           FROM research_binding rb
           JOIN research_result_index rr ON rr.result_id=rb.result_id
           WHERE rb.bond_code=? AND rb.path_id=? AND rb.is_current=1""",
        (bond_code,path_id),
    ).fetchone()


def _event_status(binding,marks: dict[str,str]) -> str:
    checked_event=binding["checked_event_watermark_hash"]
    checked_candidate=binding["checked_candidate_watermark_hash"]
    if not checked_event or not checked_candidate:
        return "UNKNOWN"
    if checked_event!=marks["confirmed"]:
        return "CHANGED_MATERIAL"
    if checked_candidate!=marks["candidate"]:
        return "CHANGED_SEMANTIC"
    return "UNCHANGED"


def apply_reuse_to_task_batch(
    *,
    data_root: Path,
    target_db: Path,
    task_batch_path: Path,
) -> dict[str,Any]:
    batch=_read_json(task_batch_path)
    pending_path=data_root/"registry"/"pending_research_tasks.json"
    state_path=data_root/"registry"/"research_trigger_state.json"
    ledger_path=data_root/"registry"/"research_ledger.json"
    pending=_read_json(pending_path)
    state=_read_json(state_path)
    ledger=_read_json(ledger_path)
    pending_by_key={x["trigger_key"]:x for x in pending.get("pending_tasks",[])}

    conn=connect(target_db)
    decisions=[]
    remaining=[]
    reused_keys=set()
    now=_now()
    try:
        for package in batch.get("tasks",[]):
            task=_read_json(Path(package["task_path"]))
            code=str(task["bond_code"]).zfill(6)
            path_id=str(task["path_id"])
            reason=str((task.get("trigger_context") or {}).get("trigger_reason") or "")
            if reason not in KEEP_ENTRY_REASONS:
                decisions.append({
                    "trigger_key":task["trigger_key"],"bond_code":code,"path_id":path_id,
                    "research_action":FULL_V2_RESEARCH,
                    "reason":"TRIGGER_IS_FORCED_REFRESH_OR_EVENT_NODE",
                })
                remaining.append(package)
                continue

            binding=_binding_row(conn,code,path_id)
            if binding is None or binding["validity_status"]!="VALID":
                decisions.append({
                    "trigger_key":task["trigger_key"],"bond_code":code,"path_id":path_id,
                    "research_action":FULL_V2_RESEARCH,
                    "reason":"NO_VALID_CURRENT_BINDING",
                })
                remaining.append(package)
                continue

            previous_task_path=data_root/"research_tasks"/f"{binding['result_task_id']}.json"
            result_path=Path(str(binding["artifact_path"]))
            if not result_path.is_absolute():
                result_path=data_root.parent/result_path
            if not previous_task_path.exists() or not result_path.exists():
                decisions.append({
                    "trigger_key":task["trigger_key"],"bond_code":code,"path_id":path_id,
                    "research_action":FULL_V2_RESEARCH,
                    "reason":"PREVIOUS_ARTIFACT_MISSING",
                })
                remaining.append(package)
                continue

            previous_task=_read_json(previous_task_path)
            previous_result=_read_json(result_path)
            marks=bond_event_watermarks(conn,code)
            event_status=_event_status(binding,marks)
            compatible=_canonical_compatible(
                data_root=data_root,previous_task=previous_task,current_task=task
            )
            decision=evaluate_research_reuse(
                previous_task=previous_task,
                previous_result=previous_result,
                current_task=task,
                event_watermark_status=event_status,
                canonical_compatible=compatible,
            )
            decision.update({
                "trigger_key":task["trigger_key"],
                "current_confirmed_watermark":marks["confirmed"],
                "current_candidate_watermark":marks["candidate"],
            })
            decisions.append(decision)

            if decision.get("research_action")!=REUSE_PREVIOUS:
                # No bounded gap-audit task exists for UNKNOWN/changed watermarks;
                # retain the normal Full-V2 task as the fail-closed execution path.
                remaining.append(package)
                continue

            old_entry=next(
                (
                    x for x in ledger.get("results",{}).values()
                    if x.get("path_result_id")==binding["result_id"]
                ),
                None,
            )
            if old_entry is None:
                remaining.append(package)
                decision["research_action"]=FULL_V2_RESEARCH
                decision["reason"]="REUSE_LEDGER_SOURCE_MISSING"
                continue

            new_entry=dict(old_entry)
            new_entry.update({
                "trigger_key":task["trigger_key"],
                "binding_type":"REUSED",
                "reuse_from_trigger_key":old_entry.get("trigger_key"),
                "reuse_reason":decision.get("reason"),
                "reuse_checked_at":now,
                "checked_event_watermark_hash":marks["confirmed"],
                "checked_candidate_watermark_hash":marks["candidate"],
                "completed_at":now,
            })
            ledger.setdefault("results",{})[task["trigger_key"]]=new_entry

            state_key=f"{code}:{path_id}"
            state_row=(state.get("paths") or {}).get(state_key)
            if state_row is None or state_row.get("last_trigger_key")!=task["trigger_key"]:
                raise RuntimeError(f"reuse trigger state mismatch: {state_key}")
            state_row["research_status"]="COMPLETED"
            state_row["last_path_result_id"]=binding["result_id"]
            state_row["research_binding_type"]="REUSED"
            state_row["reuse_from_path_result_id"]=binding["result_id"]
            state_row["reuse_reason"]=decision.get("reason")
            state_row["updated_at"]=now
            reused_keys.add(task["trigger_key"])

        pending["pending_tasks"]=[
            x for x in pending.get("pending_tasks",[])
            if x.get("trigger_key") not in reused_keys
        ]
        pending["updated_at"]=now
        state["updated_at"]=now
        ledger["updated_at"]=now
        _write_json(pending_path,pending)
        _write_json(state_path,state)
        _write_json(ledger_path,ledger)
    finally:
        conn.close()

    filtered=dict(batch)
    filtered["unit"]="PATH_RESEARCH_TASK_BUILDER_AFTER_REUSE"
    filtered["created_at"]=_now()
    filtered["tasks"]=remaining
    filtered["task_packages_built"]=len(remaining)
    filtered["pending_queue_count"]=len(pending.get("pending_tasks",[]))
    filtered["reuse_runtime_version"]=REUSE_RUNTIME_VERSION
    filtered["reused_count"]=len(reused_keys)
    filtered["reuse_decisions"]=decisions
    filtered_path=task_batch_path.with_name("path_research_task_batch_after_reuse.json")
    _write_json(filtered_path,filtered)

    return {
        "reuse_runtime_version":REUSE_RUNTIME_VERSION,
        "status":"PASS",
        "input_task_count":len(batch.get("tasks",[])),
        "reused_count":len(reused_keys),
        "remaining_task_count":len(remaining),
        "remaining_pending_count":len(pending.get("pending_tasks",[])),
        "reused_trigger_keys":sorted(reused_keys),
        "decisions":decisions,
        "effective_task_batch_path":str(filtered_path),
    }
