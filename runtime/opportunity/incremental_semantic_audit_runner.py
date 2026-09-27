"""API / Interactive-Chat runner for EVENT_SEMANTIC_AUDIT."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.ai_runtime.engine import run_ai_job
from runtime.ai_runtime.tools.evidence import (
    ToolContext,
    prefetch_event_semantic_evidence,
)
from runtime.opportunity.incremental_semantic_audit import (
    apply_event_semantic_audit_result,
)
from runtime.opportunity.incremental_storage import connect


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


def _set_trigger_status(
    *,
    target_db: Path,
    trigger_key: str,
    status: str,
) -> None:
    conn = connect(target_db)
    try:
        row = conn.execute(
            "SELECT 1 FROM research_trigger_ledger WHERE trigger_key=?",
            (trigger_key,),
        ).fetchone()
        if row is None:
            raise KeyError(f"semantic trigger not found: {trigger_key}")
        conn.execute(
            """UPDATE research_trigger_ledger
               SET status=?,updated_at=? WHERE trigger_key=?""",
            (status, _now(), trigger_key),
        )
        conn.commit()
    finally:
        conn.close()


def create_event_semantic_chat_task(
    *,
    root: Path,
    data_root: Path,
    task_id: str,
    target_db: Path,
    writeback_mode: str = "FORMAL",
) -> dict[str, Any]:
    source_path = data_root / "event_semantic_tasks" / f"{task_id}.json"
    source = _read_json(source_path)
    chat_root = data_root / "chat_tasks"
    chat_root.mkdir(parents=True, exist_ok=True)

    for existing_path in sorted(chat_root.glob("*/chat_task.json")):
        try:
            existing = _read_json(existing_path)
        except Exception:
            continue
        if (
            existing.get("task_type") == "EVENT_SEMANTIC_AUDIT"
            and existing.get("source_event_semantic_task_id") == task_id
            and existing.get("status")
            in {"WAITING_FOR_CHAT", "CLAIMED_BY_CHAT"}
        ):
            return existing

    work_dir = data_root / "event_semantic_work" / task_id
    input_path = source_path
    chat_task_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_event_sem_chat_"
        + uuid.uuid4().hex[:8]
    )
    chat_dir = chat_root / chat_task_id
    chat_dir.mkdir(parents=True, exist_ok=False)
    input_payload = _read_json(input_path)
    _write_json(chat_dir / "input.json", input_payload)

    prompt_path = (
        root / "runtime" / "ai_runtime" / "prompts"
        / "event_semantic_audit_v1.md"
    )
    (chat_dir / "prompt_snapshot.md").write_text(
        prompt_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    ctx = ToolContext(
        business_run_dir=work_dir,
        ai_job_dir=chat_dir,
        input_payload=input_payload,
    )
    prefetched = prefetch_event_semantic_evidence(ctx, max_docs=3)
    _write_json(
        chat_dir / "engineering_prefetched_evidence.json",
        {"evidence": prefetched},
    )

    task = {
        "task_id": chat_task_id,
        "task_type": "EVENT_SEMANTIC_AUDIT",
        "execution_mode": "INTERACTIVE_CHAT",
        "status": "WAITING_FOR_CHAT",
        "created_at": _now(),
        "business_run_dir": str(work_dir.resolve()),
        "source_event_semantic_task_id": task_id,
        "bond_code": source["bond_code"],
        "bond_name": source["bond_name"],
        "trigger_key": source["trigger_key"],
        "audit_subject_type": source["audit_subject_type"],
        "writeback_mode": writeback_mode,
        "database_path": str(target_db.resolve()),
        "expected_output": "event_semantic_audit_result.json",
        "prefetched_evidence_count": len(prefetched),
        "chat_instruction": (
            "处理 EVENT_SEMANTIC_AUDIT。只能使用 input.json 中冻结 Evidence "
            "及 Engineering 预取正文；输出符合 Event Semantic Audit Contract "
            "的结构化结果。Validator PASS 后由 Engineering 正式写回。"
        ),
    }
    _write_json(chat_dir / "chat_task.json", task)
    _set_trigger_status(
        target_db=target_db,
        trigger_key=source["trigger_key"],
        status="WAITING_FOR_CHAT",
    )
    return task


def run_one_event_semantic_audit(
    *,
    root: Path,
    data_root: Path,
    task_id: str,
    target_db: Path,
    provider_name: str,
    model: str,
    temperature: float | None = 0.0,
    max_tokens: int | None = 5000,
) -> dict[str, Any]:
    task_path = data_root / "event_semantic_tasks" / f"{task_id}.json"
    task = _read_json(task_path)
    work_dir = data_root / "event_semantic_work" / task_id
    _set_trigger_status(
        target_db=target_db,
        trigger_key=task["trigger_key"],
        status="IN_PROGRESS",
    )
    try:
        meta = run_ai_job(
            root=root,
            data_root=data_root,
            task_type="EVENT_SEMANTIC_AUDIT",
            input_file=task_path,
            business_run_dir=work_dir,
            provider_name=provider_name,
            provider_config={},
            model_config={
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )
        if meta.get("status") != "PASS":
            _set_trigger_status(
                target_db=target_db,
                trigger_key=task["trigger_key"],
                status="NEEDS_REVIEW",
            )
            return {
                "task_id": task_id,
                "status": meta.get("status"),
                "ai_job": meta,
                "writeback": None,
            }

        output_path = (
            data_root / "ai_jobs" / meta["ai_job_id"]
            / "structured_output.json"
        )
        writeback = apply_event_semantic_audit_result(
            result_path=output_path,
            data_root=data_root,
            target_db=target_db,
        )
        return {
            "task_id": task_id,
            "status": "PASS",
            "ai_job": meta,
            "writeback": writeback,
        }
    except Exception:
        _set_trigger_status(
            target_db=target_db,
            trigger_key=task["trigger_key"],
            status="FAIL",
        )
        raise

