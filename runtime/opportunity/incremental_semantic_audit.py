"""EVENT_SEMANTIC_AUDIT task builder, validator, and deterministic writeback."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_change_notification import (
    attach_notification_decision,
    event_changes,
)
from runtime.opportunity.incremental_event_router import route_event
from runtime.opportunity.incremental_notice_lane import (
    NOTICE_LANE_VERSION,
    _economic_statuses,
    _event_watermark,
    _persist_change_rows,
)
from runtime.opportunity.incremental_research_trigger import (
    plan_event_research_actions,
)
from runtime.opportunity.incremental_storage import connect, json_text

SEMANTIC_AUDIT_VERSION = "event-semantic-audit-v1"
OPPORTUNITY_PATH_SCOPES = {
    "MATURITY_CASH",
    "PUT",
    "DOWNWARD_REVISION",
}
INFORMATION_CANONICAL = (
    "05 套利研究/AI-Engineering-Runtime/04_数据与状态系统/"
    "Daily-Incremental-Information-Lane-V1.md"
)
ALLOWED_EVENT_FAMILIES = {
    "CREDIT:DEBT_OVERDUE",
    "CREDIT:GUARANTEE_OVERDUE",
    "CREDIT:SHARE_FREEZE",
    "CREDIT:RESTRUCTURING",
    "CREDIT:DEFAULT",
    "CREDIT:GOING_CONCERN",
    "CREDIT:HARD_OTHER",
    "CREDIT:RATING_UPDATE",
    "CREDIT:FINANCING_SUPPORT",
    "CREDIT:SUPPORT_OR_ASSET",
    "FINANCIAL:PERIODIC_REPORT",
    "REVISION:NO_REVISION_CYCLE",
    "REVISION:EXPECTED_TRIGGER",
    "REVISION:CONDITION_MET",
    "REVISION:BOARD_PROPOSAL",
    "REVISION:GOVERNANCE_ACTION",
    "REVISION:FINAL_K_CHANGE",
    "PUT:EXPECTED_TRIGGER",
    "PUT:CONDITION_MET",
    "PUT:RESULT",
}
DISPOSITIONS = {
    "CONFIRMED_EVENT_UPDATE",
    "NO_MATERIAL_CHANGE",
    "NEEDS_EVIDENCE",
    "SCOPE_FULL_V2_RESEARCH",
    "SCOPE_NO_RESEARCH",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _task_id(trigger_key: str) -> str:
    return "event_sem_" + hashlib.sha256(
        trigger_key.encode("utf-8")
    ).hexdigest()[:20]


def _result_id(task_id: str) -> str:
    return "esa_" + task_id


def _semantic_event_update_id(
    bond_code: str,
    event_family: str,
    occurred_at: str,
) -> str:
    raw = f"{str(bond_code).zfill(6)}|{event_family}|{occurred_at}"
    return "EVU_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _document_evidence_id_from_trigger(trigger_key: str) -> str | None:
    match = re.fullmatch(
        r"EVIDENCE:([^:]+):(\d{6}):SEMANTIC_AUDIT",
        trigger_key,
    )
    return match.group(1) if match else None


def _knowledge_canonical(
    data_root: Path,
    knowledge_sha: str,
) -> dict[str, Any]:
    snapshot = data_root / "knowledge_snapshots" / knowledge_sha
    manifest_path = snapshot / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"knowledge snapshot missing: {knowledge_sha}"
        )
    manifest = _read_json(manifest_path)
    item = next(
        (
            row for row in manifest.get("files", [])
            if row.get("canonical_path") == INFORMATION_CANONICAL
        ),
        None,
    )
    if item is None:
        raise RuntimeError(
            "Information Lane Canonical is not in knowledge snapshot"
        )
    local_path = snapshot / str(item["local_file"])
    return {
        "canonical_path": INFORMATION_CANONICAL,
        "sha256": item["sha256"],
        "text": local_path.read_text(encoding="utf-8"),
    }


def _evidence_rows(
    conn,
    evidence_ids: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        row = conn.execute(
            """SELECT evidence_id,issuer_stock_code,published_at,title,
                      source_kind,url,content_sha256,artifact_path,metadata_json
               FROM evidence_document WHERE evidence_id=?""",
            (evidence_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"evidence document not found: {evidence_id}")
        payload = dict(row)
        payload["metadata"] = json.loads(
            payload.pop("metadata_json") or "{}"
        )
        rows.append(payload)
    return rows


def _existing_event_context(conn, bond_code: str) -> list[dict[str, Any]]:
    families = []
    for family in conn.execute(
        """SELECT event_family_id,event_family,latest_confirmed_version,
                  confirmed_watermark,semantic_candidate_watermark
           FROM event_family WHERE bond_code=? ORDER BY event_family""",
        (bond_code,),
    ):
        updates = [
            dict(row)
            for row in conn.execute(
                """SELECT event_update_id,confirmation_status,event_version,
                          candidate_version,occurred_at,materiality_status
                   FROM event_update WHERE event_family_id=?
                   ORDER BY COALESCE(event_version,999999),
                            COALESCE(candidate_version,999999),
                            occurred_at""",
                (family["event_family_id"],),
            )
        ]
        families.append({
            "event_family": family["event_family"],
            "latest_confirmed_version": family[
                "latest_confirmed_version"
            ],
            "confirmed_watermark": family["confirmed_watermark"],
            "semantic_candidate_watermark": family[
                "semantic_candidate_watermark"
            ],
            "updates": updates,
        })
    return families


def build_event_semantic_audit_tasks(
    *,
    data_root: Path,
    target_db: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    knowledge_sha = str(deployment.get("knowledge_commit_sha") or "")
    if not knowledge_sha:
        raise RuntimeError("deployment has no knowledge_commit_sha")
    canonical = _knowledge_canonical(data_root, knowledge_sha)
    conn = connect(target_db)
    task_store = data_root / "event_semantic_tasks"
    work_root = data_root / "event_semantic_work"
    task_store.mkdir(parents=True, exist_ok=True)
    work_root.mkdir(parents=True, exist_ok=True)

    rows = list(conn.execute(
        """SELECT rtl.trigger_key,rtl.bond_code,rtl.path_id,
                  rtl.source_event_update_id,rtl.status,rtl.emitted_at,
                  b.bond_name,b.stock_code,eu.occurred_at,
                  eu.confirmation_status AS source_confirmation_status,
                  eu.materiality_status AS source_materiality_status,
                  eu.payload_json AS source_event_payload_json,
                  ef.event_family
           FROM research_trigger_ledger rtl
           JOIN bond_master b ON b.bond_code=rtl.bond_code
           LEFT JOIN event_update eu
             ON eu.event_update_id=rtl.source_event_update_id
           LEFT JOIN event_family ef
             ON ef.event_family_id=eu.event_family_id
           WHERE rtl.task_kind='EVENT_SEMANTIC_AUDIT'
             AND rtl.status='PENDING'
           ORDER BY rtl.emitted_at,rtl.trigger_key"""
    ))
    packages = []
    now = _now()
    try:
        for row in rows:
            trigger_key = row["trigger_key"]
            task_id = _task_id(trigger_key)
            source_event_id = row["source_event_update_id"]
            if source_event_id and row["source_confirmation_status"] == "SEMANTIC_CANDIDATE":
                subject_type = "SEMANTIC_CANDIDATE"
            elif source_event_id and row["path_id"]:
                subject_type = "SCOPE_IMPACT"
            elif source_event_id:
                raise RuntimeError(
                    f"confirmed Event semantic trigger has no target scope: {trigger_key}"
                )
            else:
                subject_type = "DOCUMENT_ONLY"
            if source_event_id:
                evidence_ids = [
                    x["evidence_id"]
                    for x in conn.execute(
                        """SELECT evidence_id FROM event_evidence_link
                           WHERE event_update_id=? ORDER BY evidence_id""",
                        (source_event_id,),
                    )
                ]
            else:
                evidence_id = _document_evidence_id_from_trigger(
                    trigger_key
                )
                if not evidence_id:
                    raise RuntimeError(
                        f"cannot resolve Evidence ID from {trigger_key}"
                    )
                evidence_ids = [evidence_id]

            evidence_documents = _evidence_rows(conn, evidence_ids)
            dates = [
                str(x.get("published_at") or "")[:10]
                for x in evidence_documents
                if x.get("published_at")
            ]
            research_cutoff = max(dates) if dates else str(
                row["occurred_at"] or ""
            )[:10]
            if not research_cutoff:
                raise RuntimeError(
                    f"semantic audit has no research cutoff: {trigger_key}"
                )

            current_scopes = [
                {
                    "scope_type": x["scope_type"],
                    "scope_id": x["scope_id"],
                    "economic_status": x["economic_status"],
                    "state_code": x["state_code"],
                    "state_version": x["state_version"],
                    "research_state_version": x[
                        "research_state_version"
                    ],
                }
                for x in conn.execute(
                    """SELECT scope_type,scope_id,economic_status,state_code,
                              state_version,research_state_version
                       FROM scope_state_current
                       WHERE bond_code=?
                       ORDER BY scope_type,scope_order,scope_id""",
                    (row["bond_code"],),
                )
            ]
            payload = {
                "task_type": "EVENT_SEMANTIC_AUDIT",
                "semantic_audit_version": SEMANTIC_AUDIT_VERSION,
                "task_id": task_id,
                "expected_audit_result_id": _result_id(task_id),
                "trigger_key": trigger_key,
                "trigger_source": "EVIDENCE_EVENT",
                "bond_code": row["bond_code"],
                "bond_name": row["bond_name"],
                "issuer_stock_code": str(
                    row["stock_code"] or ""
                ).zfill(6),
                "audit_subject_type": subject_type,
                "source_event_update_id": source_event_id,
                "candidate_event_family": (
                    row["event_family"]
                    if subject_type == "SEMANTIC_CANDIDATE"
                    else None
                ),
                "target_scope_id": (
                    row["path_id"] if subject_type == "SCOPE_IMPACT" else None
                ),
                "source_event_family": row["event_family"],
                "source_event_confirmation_status": row["source_confirmation_status"],
                "source_event_materiality_status": row["source_materiality_status"],
                "source_event_payload": (
                    json.loads(row["source_event_payload_json"] or "{}")
                    if row["source_event_payload_json"]
                    else None
                ),
                "research_cutoff": research_cutoff,
                "evidence_documents": evidence_documents,
                "existing_event_context": _existing_event_context(
                    conn, row["bond_code"]
                ),
                "current_scope_context": current_scopes,
                "allowed_event_families": sorted(
                    ALLOWED_EVENT_FAMILIES
                ),
                "knowledge_commit_sha": knowledge_sha,
                "canonical_context": canonical,
                "rules": {
                    "do_not_expand_beyond_frozen_evidence": True,
                    "new_document_is_not_automatically_new_event": True,
                    "semantic_candidate_can_only_confirm_candidate_family": True,
                    "document_only_may_confirm_multiple_specific_events": True,
                    "scope_impact_never_reconfirms_event": True,
                    "scope_impact_only_decides_full_v2_or_no_research": True,
                    "no_economic_judgment": True,
                },
            }
            task_path = task_store / f"{task_id}.json"
            _write_json(task_path, payload)
            work_dir = work_root / task_id
            work_dir.mkdir(parents=True, exist_ok=True)
            _write_json(
                work_dir / "semantic_audit_input.json",
                payload,
            )
            conn.execute(
                """UPDATE research_trigger_ledger
                   SET status='READY_FOR_AI_AUDIT',task_id=?,updated_at=?
                   WHERE trigger_key=?""",
                (task_id, now, trigger_key),
            )
            packages.append({
                "task_id": task_id,
                "trigger_key": trigger_key,
                "bond_code": row["bond_code"],
                "bond_name": row["bond_name"],
                "audit_subject_type": subject_type,
                "candidate_event_family": (
                    row["event_family"]
                    if subject_type == "SEMANTIC_CANDIDATE"
                    else None
                ),
                "target_scope_id": (
                    row["path_id"] if subject_type == "SCOPE_IMPACT" else None
                ),
                "research_cutoff": research_cutoff,
                "task_path": str(task_path),
                "work_dir": str(work_dir),
                "evidence_ids": evidence_ids,
            })
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    batch = {
        "semantic_audit_version": SEMANTIC_AUDIT_VERSION,
        "status": "PASS",
        "created_at": now,
        "knowledge_commit_sha": knowledge_sha,
        "task_count": len(packages),
        "tasks": packages,
    }
    pointer = data_root / "registry" / "latest_event_semantic_tasks.json"
    _write_json(pointer, batch)
    return batch


def validate_event_semantic_audit(
    run_dir: Path,
    structured_output_path: Path,
) -> dict[str, Any]:
    task_path = run_dir / "semantic_audit_input.json"
    task = _read_json(task_path)
    result = _read_json(structured_output_path)
    errors: list[str] = []

    expected_pairs = {
        "audit_result_id": task["expected_audit_result_id"],
        "task_id": task["task_id"],
        "trigger_key": task["trigger_key"],
        "bond_code": task["bond_code"],
        "research_cutoff": task["research_cutoff"],
    }
    for key, expected in expected_pairs.items():
        actual = result.get(key)
        if key == "bond_code":
            actual = str(actual or "").zfill(6)
        if actual != expected:
            errors.append(
                f"{key} mismatch: expected={expected!r}, actual={actual!r}"
            )

    disposition = result.get("disposition")
    if disposition not in DISPOSITIONS:
        errors.append("invalid disposition")

    confirmed = result.get("confirmed_events")
    if not isinstance(confirmed, list):
        errors.append("confirmed_events must be an array")
        confirmed = []

    subject_type = task["audit_subject_type"]
    if subject_type == "SCOPE_IMPACT":
        if disposition not in {
            "SCOPE_FULL_V2_RESEARCH",
            "SCOPE_NO_RESEARCH",
            "NEEDS_EVIDENCE",
        }:
            errors.append(
                "SCOPE_IMPACT requires SCOPE_FULL_V2_RESEARCH, "
                "SCOPE_NO_RESEARCH, or NEEDS_EVIDENCE"
            )
        if confirmed:
            errors.append(
                "SCOPE_IMPACT must not reconfirm Event; confirmed_events=[]"
            )
        if (
            disposition == "SCOPE_FULL_V2_RESEARCH"
            and task.get("target_scope_id") not in OPPORTUNITY_PATH_SCOPES
        ):
            errors.append(
                "SCOPE_FULL_V2_RESEARCH is only valid for opportunity Path scopes"
            )
    else:
        if disposition not in {
            "CONFIRMED_EVENT_UPDATE",
            "NO_MATERIAL_CHANGE",
            "NEEDS_EVIDENCE",
        }:
            errors.append(
                "Event audit requires CONFIRMED_EVENT_UPDATE, "
                "NO_MATERIAL_CHANGE, or NEEDS_EVIDENCE"
            )
        if disposition == "CONFIRMED_EVENT_UPDATE" and not confirmed:
            errors.append(
                "CONFIRMED_EVENT_UPDATE requires confirmed_events"
            )
        if disposition in {"NO_MATERIAL_CHANGE", "NEEDS_EVIDENCE"} and confirmed:
            errors.append(
                f"{disposition} requires confirmed_events=[]"
            )

    if (
        subject_type == "SEMANTIC_CANDIDATE"
        and disposition == "CONFIRMED_EVENT_UPDATE"
        and len(confirmed) != 1
    ):
        errors.append(
            "SEMANTIC_CANDIDATE can confirm exactly one Event"
        )

    frozen_ids = {
        str(x["evidence_id"])
        for x in task.get("evidence_documents", [])
    }
    candidate_family = task.get("candidate_event_family")
    for idx, event in enumerate(confirmed):
        family = event.get("event_family")
        if family not in ALLOWED_EVENT_FAMILIES:
            errors.append(
                f"confirmed_events[{idx}].event_family not allowed"
            )
        if (
            task["audit_subject_type"] == "SEMANTIC_CANDIDATE"
            and family != candidate_family
        ):
            errors.append(
                "semantic candidate may only confirm candidate_event_family"
            )
        occurred_at = str(event.get("occurred_at") or "")[:10]
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", occurred_at):
            errors.append(
                f"confirmed_events[{idx}].occurred_at invalid"
            )
        elif occurred_at > task["research_cutoff"]:
            errors.append(
                f"confirmed_events[{idx}].occurred_at exceeds cutoff"
            )
        evidence_ids = event.get("supporting_evidence_ids")
        if not isinstance(evidence_ids, list) or not evidence_ids:
            errors.append(
                f"confirmed_events[{idx}] has no supporting evidence"
            )
        elif not set(map(str, evidence_ids)) <= frozen_ids:
            errors.append(
                f"confirmed_events[{idx}] references non-frozen evidence"
            )
        if not str(event.get("fact_summary") or "").strip():
            errors.append(
                f"confirmed_events[{idx}].fact_summary is empty"
            )

    validation = {
        "status": "PASS" if not errors else "FAIL",
        "semantic_audit_version": SEMANTIC_AUDIT_VERSION,
        "task_id": task["task_id"],
        "errors": errors,
    }
    _write_json(run_dir / "semantic_audit_validation.json", validation)
    return validation


def _recompute_family_watermarks(
    conn,
    family_id: str,
    updated_at: str,
) -> None:
    confirmed = [
        row["event_update_id"]
        for row in conn.execute(
            """SELECT event_update_id FROM event_update
               WHERE event_family_id=?
                 AND confirmation_status='CONFIRMED'
               ORDER BY event_version""",
            (family_id,),
        )
    ]
    candidates = [
        row["event_update_id"]
        for row in conn.execute(
            """SELECT event_update_id FROM event_update
               WHERE event_family_id=?
                 AND confirmation_status='SEMANTIC_CANDIDATE'
               ORDER BY candidate_version""",
            (family_id,),
        )
    ]
    conn.execute(
        """UPDATE event_family
           SET latest_confirmed_version=?,
               confirmed_watermark=?,
               semantic_candidate_watermark=?,
               updated_at=?
           WHERE event_family_id=?""",
        (
            len(confirmed),
            _event_watermark("EWM", confirmed),
            _event_watermark("ECM", candidates),
            updated_at,
            family_id,
        ),
    )


def _route_confirmed_event(
    *,
    conn,
    bond_code: str,
    bond_name: str,
    event_update_id: str,
    event_family: str,
    materiality: str,
    detected_at: str,
) -> dict[str, Any]:
    event = {
        "event_update_id": event_update_id,
        "event_family": event_family,
        "materiality": materiality,
        "requires_semantic_audit": False,
    }
    routing = route_event(event)
    for route in routing.get("routes", []):
        scope = route["scope"]
        scope_type = "RISK" if scope.endswith("_RISK") else "PATH"
        conn.execute(
            """INSERT OR REPLACE INTO event_scope_impact
               (event_update_id,scope_type,scope_id,impact,
                research_action,notification_hint,reason,route_version)
               VALUES (?,?,?,?,?,?,?,?)""",
            (
                event_update_id,
                scope_type,
                scope,
                route["impact"],
                route["research_action"],
                route["notification"],
                route["reason"],
                "incremental-event-router-v1",
            ),
        )

    existing_keys = {
        row["trigger_key"]
        for row in conn.execute(
            "SELECT trigger_key FROM research_trigger_ledger"
        )
    }
    plan = plan_event_research_actions(
        event=event,
        routing=routing,
        economic_status_by_scope=_economic_statuses(
            conn, bond_code
        ),
        previously_emitted_keys=existing_keys,
    )
    for action in plan.get("all_actions", []):
        conn.execute(
            """INSERT OR IGNORE INTO research_trigger_ledger
               (trigger_key,bond_code,path_id,source_event_update_id,
                research_action,task_kind,status,task_id,
                emitted_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                action["trigger_key"],
                bond_code,
                action["scope"],
                event_update_id,
                action["research_action"],
                action["task_kind"],
                action["task_status"],
                None,
                detected_at,
                detected_at,
            ),
        )

    changes = [
        attach_notification_decision(row)
        for row in event_changes(
            bond_code=bond_code,
            bond_name=bond_name,
            event=event,
            routing=routing,
        )
    ]
    change_counts = _persist_change_rows(
        conn, changes, detected_at
    )
    return {
        "routing": routing,
        "research_plan": plan,
        "change_counts": change_counts,
    }


def _apply_scope_impact_result(
    *,
    conn,
    task: dict[str, Any],
    result: dict[str, Any],
    now: str,
) -> dict[str, Any]:
    scope = str(task.get("target_scope_id") or "")
    source_event_id = str(task.get("source_event_update_id") or "")
    disposition = str(result.get("disposition") or "")
    if not scope or not source_event_id:
        raise RuntimeError("SCOPE_IMPACT task missing scope/event identity")

    if disposition == "NEEDS_EVIDENCE":
        conn.execute(
            """UPDATE research_trigger_ledger
               SET status='NEEDS_EVIDENCE',updated_at=?
               WHERE trigger_key=?""",
            (now, task["trigger_key"]),
        )
        return {
            "target_scope_id": scope,
            "resolved_research_action": "SEMANTIC_AUDIT",
            "full_v2_trigger_key": None,
            "status": "NEEDS_EVIDENCE",
        }

    if disposition == "SCOPE_NO_RESEARCH":
        conn.execute(
            """UPDATE event_scope_impact
               SET research_action='NONE',
                   reason='SEMANTIC_SCOPE_RESOLVED_NO_RESEARCH'
               WHERE event_update_id=? AND scope_id=?""",
            (source_event_id, scope),
        )
        conn.execute(
            """UPDATE research_trigger_ledger
               SET status='COMPLETED_SCOPE_NO_RESEARCH',updated_at=?
               WHERE trigger_key=?""",
            (now, task["trigger_key"]),
        )
        return {
            "target_scope_id": scope,
            "resolved_research_action": "NONE",
            "full_v2_trigger_key": None,
            "status": "COMPLETED_SCOPE_NO_RESEARCH",
        }

    if disposition != "SCOPE_FULL_V2_RESEARCH":
        raise RuntimeError(f"unsupported SCOPE_IMPACT disposition={disposition!r}")
    if scope not in OPPORTUNITY_PATH_SCOPES:
        raise RuntimeError(
            f"FULL_V2 scope must be opportunity Path, got {scope!r}"
        )

    state = conn.execute(
        """SELECT economic_status FROM scope_state_current
           WHERE bond_code=? AND scope_type='PATH' AND scope_id=?""",
        (task["bond_code"], scope),
    ).fetchone()
    economic_status = state["economic_status"] if state else None
    trigger_key = (
        f"EVENT:{source_event_id}:{scope}:FULL_V2_RESEARCH"
    )
    trigger_status = (
        "PENDING" if economic_status == "KEEP"
        else "SUPPRESSED_ECONOMIC_DROP"
    )
    conn.execute(
        """INSERT OR IGNORE INTO research_trigger_ledger
           (trigger_key,bond_code,path_id,source_event_update_id,
            research_action,task_kind,status,task_id,emitted_at,updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            trigger_key,
            task["bond_code"],
            scope,
            source_event_id,
            "FULL_V2_RESEARCH",
            "PATH_RESEARCH",
            trigger_status,
            None,
            now,
            now,
        ),
    )
    conn.execute(
        """UPDATE event_scope_impact
           SET research_action='FULL_V2_RESEARCH',
               reason='SEMANTIC_SCOPE_RESOLVED_FULL_V2'
           WHERE event_update_id=? AND scope_id=?""",
        (source_event_id, scope),
    )
    conn.execute(
        """UPDATE research_trigger_ledger
           SET status='COMPLETED_SCOPE_FULL_V2',updated_at=?
           WHERE trigger_key=?""",
        (now, task["trigger_key"]),
    )
    return {
        "target_scope_id": scope,
        "resolved_research_action": "FULL_V2_RESEARCH",
        "full_v2_trigger_key": trigger_key,
        "full_v2_trigger_status": trigger_status,
        "economic_status": economic_status,
        "status": "COMPLETED_SCOPE_FULL_V2",
    }


def apply_event_semantic_audit_result(
    *,
    result_path: Path,
    data_root: Path,
    target_db: Path,
) -> dict[str, Any]:
    result = _read_json(result_path)
    task_id = str(result.get("task_id") or "")
    work_dir = data_root / "event_semantic_work" / task_id
    validation = validate_event_semantic_audit(
        work_dir, result_path
    )
    if validation["status"] != "PASS":
        raise RuntimeError(
            "semantic audit result failed Program Validator"
        )
    task = _read_json(
        data_root / "event_semantic_tasks" / f"{task_id}.json"
    )
    conn = connect(target_db)
    now = _now()
    confirmed_outputs: list[dict[str, Any]] = []
    scope_impact_output: dict[str, Any] | None = None
    try:
        trigger = conn.execute(
            """SELECT status,source_event_update_id
               FROM research_trigger_ledger
               WHERE trigger_key=?""",
            (task["trigger_key"],),
        ).fetchone()
        if trigger is None:
            raise KeyError("semantic audit trigger not found")

        disposition = result["disposition"]
        source_event_id = task.get("source_event_update_id")
        if task["audit_subject_type"] == "SCOPE_IMPACT":
            scope_impact_output = _apply_scope_impact_result(
                conn=conn,
                task=task,
                result=result,
                now=now,
            )
        elif disposition == "NEEDS_EVIDENCE":
            conn.execute(
                """UPDATE research_trigger_ledger
                   SET status='NEEDS_EVIDENCE',updated_at=?
                   WHERE trigger_key=?""",
                (now, task["trigger_key"]),
            )
        elif disposition == "NO_MATERIAL_CHANGE":
            conn.execute(
                """UPDATE research_trigger_ledger
                   SET status='COMPLETED_NO_MATERIAL_CHANGE',updated_at=?
                   WHERE trigger_key=?""",
                (now, task["trigger_key"]),
            )
            if source_event_id:
                row = conn.execute(
                    """SELECT event_family_id FROM event_update
                       WHERE event_update_id=?""",
                    (source_event_id,),
                ).fetchone()
                if row:
                    conn.execute(
                        """UPDATE event_update
                           SET confirmation_status='REJECTED',
                               materiality_status='NO_MATERIAL_CHANGE',
                               payload_json=?
                           WHERE event_update_id=?""",
                        (
                            json_text({
                                "semantic_audit_result_id": result[
                                    "audit_result_id"
                                ],
                                "reason_short": result["reason_short"],
                            }),
                            source_event_id,
                        ),
                    )
                    _recompute_family_watermarks(
                        conn, row["event_family_id"], now
                    )
        else:
            for item in result["confirmed_events"]:
                family = item["event_family"]
                occurred_at = str(item["occurred_at"])[:10]
                family_id = f"{task['bond_code']}:{family}"
                conn.execute(
                    """INSERT OR IGNORE INTO event_family
                       (event_family_id,bond_code,event_family,
                        latest_confirmed_version,confirmed_watermark,
                        semantic_candidate_watermark,updated_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        family_id,
                        task["bond_code"],
                        family,
                        0,
                        "EWM_EMPTY",
                        "ECM_EMPTY",
                        now,
                    ),
                )

                if source_event_id:
                    event_id = source_event_id
                    next_version = conn.execute(
                        """SELECT COALESCE(MAX(event_version),0)+1 n
                           FROM event_update
                           WHERE event_family_id=?
                             AND confirmation_status='CONFIRMED'""",
                        (family_id,),
                    ).fetchone()["n"]
                    conn.execute(
                        """UPDATE event_update
                           SET confirmation_status='CONFIRMED',
                               event_version=?,candidate_version=NULL,
                               occurred_at=?,materiality_status=?,
                               payload_json=?
                           WHERE event_update_id=?""",
                        (
                            int(next_version),
                            occurred_at,
                            item["materiality"],
                            json_text({
                                "semantic_audit_result_id": result[
                                    "audit_result_id"
                                ],
                                "fact_summary": item["fact_summary"],
                            }),
                            event_id,
                        ),
                    )
                else:
                    event_id = _semantic_event_update_id(
                        task["bond_code"], family, occurred_at
                    )
                    existing = conn.execute(
                        """SELECT 1 FROM event_update
                           WHERE event_update_id=?""",
                        (event_id,),
                    ).fetchone()
                    if existing is None:
                        next_version = conn.execute(
                            """SELECT COALESCE(MAX(event_version),0)+1 n
                               FROM event_update
                               WHERE event_family_id=?
                                 AND confirmation_status='CONFIRMED'""",
                            (family_id,),
                        ).fetchone()["n"]
                        conn.execute(
                            """INSERT INTO event_update
                               (event_update_id,event_family_id,
                                confirmation_status,event_version,
                                candidate_version,occurred_at,
                                materiality_status,payload_json,created_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (
                                event_id,
                                family_id,
                                "CONFIRMED",
                                int(next_version),
                                None,
                                occurred_at,
                                item["materiality"],
                                json_text({
                                    "semantic_audit_result_id": result[
                                        "audit_result_id"
                                    ],
                                    "fact_summary": item["fact_summary"],
                                }),
                                now,
                            ),
                        )
                for evidence_id in item["supporting_evidence_ids"]:
                    conn.execute(
                        """INSERT OR IGNORE INTO event_evidence_link
                           (event_update_id,evidence_id,link_role)
                           VALUES (?,?,?)""",
                        (event_id, evidence_id, "SUPPORTING"),
                    )
                _recompute_family_watermarks(
                    conn, family_id, now
                )
                routed = _route_confirmed_event(
                    conn=conn,
                    bond_code=task["bond_code"],
                    bond_name=task["bond_name"],
                    event_update_id=event_id,
                    event_family=family,
                    materiality=item["materiality"],
                    detected_at=now,
                )
                confirmed_outputs.append({
                    "event_update_id": event_id,
                    "event_family": family,
                    **routed,
                })
            conn.execute(
                """UPDATE research_trigger_ledger
                   SET status='COMPLETED_CONFIRMED',updated_at=?
                   WHERE trigger_key=?""",
                (now, task["trigger_key"]),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    result_store = data_root / "event_semantic_results"
    result_store.mkdir(parents=True, exist_ok=True)
    canonical_result = result_store / (
        f"{result['audit_result_id']}.json"
    )
    shutil.copy2(result_path, canonical_result)
    pointer = {
        "semantic_audit_version": SEMANTIC_AUDIT_VERSION,
        "audit_result_id": result["audit_result_id"],
        "task_id": task_id,
        "trigger_key": task["trigger_key"],
        "bond_code": task["bond_code"],
        "disposition": result["disposition"],
        "result_path": str(canonical_result),
        "applied_at": now,
    }
    _write_json(
        data_root / "registry" / "latest_event_semantic_result.json",
        pointer,
    )
    return {
        "status": "PASS",
        **pointer,
        "confirmed_outputs": confirmed_outputs,
        "scope_impact_output": scope_impact_output,
    }

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]