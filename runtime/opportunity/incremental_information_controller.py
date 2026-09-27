"""Independent Information Lane controller.

Orchestrates notice checkpoint, semantic-audit waves, Event→V2 bridge, and the
existing Path Research batch. It never reruns market discovery.
"""
from __future__ import annotations

import fcntl
import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_event_research_bridge import promote_event_path_research
from runtime.opportunity.incremental_notice_checkpoint import run_notice_checkpoint
from runtime.opportunity.incremental_semantic_audit import build_event_semantic_audit_tasks
from runtime.opportunity.incremental_semantic_audit_runner import (
    create_event_semantic_chat_task,
    run_one_event_semantic_audit,
)
from runtime.opportunity.incremental_storage import connect
from runtime.opportunity.path_research_batch import run_path_research_batch
from runtime.opportunity.research_evidence import build_research_evidence

CONTROLLER_VERSION = "incremental-information-controller-v1"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

@contextmanager
def _lock(data_root: Path):
    path=data_root/"registry"/"information_controller.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle=path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("Another Information Controller is running") from exc
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

def _semantic_status_summary(target_db: Path) -> dict[str,int]:
    conn=connect(target_db)
    try:
        rows=conn.execute(
            """select status,count(*) n from research_trigger_ledger
               where task_kind='EVENT_SEMANTIC_AUDIT'
               group by status"""
        )
        return {str(x["status"]):int(x["n"]) for x in rows}
    finally:
        conn.close()

def run_information_controller(
    *,
    root: Path,
    data_root: Path,
    target_date: str,
    deployment: dict[str,Any],
    execution_mode: str,
    provider_name: str,
    model: str,
    semantic_max_waves: int = 3,
    path_limit: int = 10,
) -> dict[str,Any]:
    if execution_mode not in {"AUTO_API","INTERACTIVE_CHAT"}:
        raise ValueError(f"unsupported execution_mode={execution_mode!r}")
    target_db=data_root/"state"/"incremental_runtime.sqlite"
    run_id=(
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        +"_information_controller_"+uuid.uuid4().hex[:8]
    )
    run_dir=data_root/"information_controller_runs"/run_id
    run_dir.mkdir(parents=True,exist_ok=False)
    state={
        "controller_version":CONTROLLER_VERSION,
        "run_id":run_id,
        "target_date":target_date,
        "execution_mode":execution_mode,
        "status":"RUNNING",
        "started_at":_now(),
        "application_commit_sha":deployment.get("application_commit_sha"),
        "knowledge_commit_sha":deployment.get("knowledge_commit_sha"),
        "stages":[],
    }
    _write_json(run_dir/"status.json",state)

    with _lock(data_root):
        try:
            checkpoint=run_notice_checkpoint(
                data_root=data_root,target_date=target_date
            )
            state["stages"].append({
                "stage":"NOTICE_CHECKPOINT",
                "status":checkpoint["status"],
                "days":checkpoint.get("days",[]),
            })
            if checkpoint["status"]!="PASS":
                raise RuntimeError("notice checkpoint did not PASS")

            semantic_waves=[]
            waiting_chat=[]
            semantic_failed=False
            for wave in range(1,max(1,int(semantic_max_waves))+1):
                batch=build_event_semantic_audit_tasks(
                    data_root=data_root,
                    target_db=target_db,
                    deployment=deployment,
                )
                tasks=batch.get("tasks",[])
                if not tasks:
                    break
                wave_result={"wave":wave,"task_count":len(tasks),"tasks":[]}

                if execution_mode=="INTERACTIVE_CHAT":
                    for task in tasks:
                        chat=create_event_semantic_chat_task(
                            root=root,
                            data_root=data_root,
                            task_id=task["task_id"],
                            target_db=target_db,
                            writeback_mode="FORMAL",
                        )
                        item={
                            "task_id":task["task_id"],
                            "bond_code":task["bond_code"],
                            "audit_subject_type":task["audit_subject_type"],
                            "target_scope_id":task.get("target_scope_id"),
                            "status":"WAITING_FOR_CHAT",
                            "chat_task_id":chat["task_id"],
                        }
                        wave_result["tasks"].append(item)
                        waiting_chat.append(item)
                    semantic_waves.append(wave_result)
                    break

                for task in tasks:
                    result=run_one_event_semantic_audit(
                        root=root,
                        data_root=data_root,
                        task_id=task["task_id"],
                        target_db=target_db,
                        provider_name=provider_name,
                        model=model,
                    )
                    item={
                        "task_id":task["task_id"],
                        "bond_code":task["bond_code"],
                        "audit_subject_type":task["audit_subject_type"],
                        "target_scope_id":task.get("target_scope_id"),
                        "status":result.get("status"),
                    }
                    wave_result["tasks"].append(item)
                    if result.get("status")!="PASS":
                        semantic_failed=True
                semantic_waves.append(wave_result)
                if semantic_failed:
                    break

            state["stages"].append({
                "stage":"EVENT_SEMANTIC_AUDIT",
                "status":(
                    "WAITING_FOR_CHAT" if waiting_chat
                    else "NEEDS_REVIEW" if semantic_failed
                    else "PASS"
                ),
                "waves":semantic_waves,
                "status_summary":_semantic_status_summary(target_db),
            })

            bridge=promote_event_path_research(
                data_root=data_root,
                target_db=target_db,
                deployment=deployment,
            )
            state["stages"].append({
                "stage":"EVENT_RESEARCH_BRIDGE",
                "status":bridge["status"],
                "promoted_count":bridge["promoted_count"],
                "conflict_count":bridge["conflict_count"],
                "deferred_count":bridge["deferred_count"],
            })

            path_batch=None
            evidence_batch=None
            path_waiting=0
            if bridge["promoted_count"]>0:
                task_batch=bridge.get("task_batch") or {}
                task_batch_path=(
                    data_root/"runs"/str(task_batch["run_id"])
                    /"path_research_task_batch.json"
                )
                evidence_batch=build_research_evidence(
                    task_batch_path=task_batch_path,
                    data_root=data_root,
                    deployment=deployment,
                )
                state["stages"].append({
                    "stage":"PATH_RESEARCH_EVIDENCE",
                    "status":evidence_batch.get("status"),
                    "task_count":evidence_batch.get("packs_built"),
                })
                path_batch=run_path_research_batch(
                    root=root,
                    data_root=data_root,
                    provider_name=provider_name,
                    model=model,
                    execution_mode=execution_mode,
                    full_runtime_run_id=None,
                    limit=path_limit,
                    retry_once=False,
                )
                path_waiting=int(
                    path_batch.get("status_counts",{}).get(
                        "WAITING_FOR_CHAT",0
                    )
                )
            if bridge["promoted_count"]==0:
                state["stages"].append({
                    "stage":"PATH_RESEARCH_EVIDENCE",
                    "status":"SKIPPED",
                    "task_count":0,
                })
            state["stages"].append({
                "stage":"PATH_RESEARCH",
                "status":"WAITING_FOR_CHAT" if path_waiting else "PASS",
                "batch":path_batch,
            })

            if semantic_failed or bridge["status"]=="NEEDS_REVIEW":
                final_status="NEEDS_REVIEW"
            elif waiting_chat or path_waiting:
                final_status="WAITING_FOR_CHAT"
            else:
                final_status="PASS"

            state["status"]=final_status
            state["completed_at"]=_now()
            state["summary"]={
                "semantic_waiting_chat":len(waiting_chat),
                "semantic_status_summary":_semantic_status_summary(target_db),
                "path_promoted":bridge["promoted_count"],
                "path_waiting_chat":path_waiting,
            }
            _write_json(run_dir/"status.json",state)
            _write_json(
                data_root/"registry"/"latest_information_controller.json",
                {
                    "run_id":run_id,
                    "target_date":target_date,
                    "execution_mode":execution_mode,
                    "status":final_status,
                    "status_path":str(run_dir/"status.json"),
                    "completed_at":state["completed_at"],
                    "summary":state["summary"],
                },
            )
            return state
        except Exception as exc:
            state["status"]="FAIL"
            state["completed_at"]=_now()
            state["error"]=f"{type(exc).__name__}: {exc}"
            _write_json(run_dir/"status.json",state)
            _write_json(
                data_root/"registry"/"latest_information_controller.json",
                {
                    "run_id":run_id,
                    "target_date":target_date,
                    "execution_mode":execution_mode,
                    "status":"FAIL",
                    "status_path":str(run_dir/"status.json"),
                    "completed_at":state["completed_at"],
                    "error":state["error"],
                },
            )
            raise

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]