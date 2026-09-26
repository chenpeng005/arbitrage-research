"""Orchestrate one PATH_RESEARCH AI job and ledger write-back."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.ai_runtime.engine import run_ai_job
from runtime.ai_runtime.tools.evidence import ToolContext, prefetch_path_research_evidence
from runtime.opportunity.research_ai_input import prepare_path_research_ai_input
from runtime.opportunity.research_ledger import record_path_research_result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def set_trigger_research_status(
    data_root: Path,
    task_id: str,
    status: str,
) -> None:
    task = _read_json(data_root / "research_tasks" / f"{task_id}.json")
    state_path = data_root / "registry" / "research_trigger_state.json"
    state = _read_json(state_path)
    key = f"{str(task['bond_code']).zfill(6)}:{task['path_id']}"
    row = state.get("paths", {}).get(key)
    if row is None:
        raise KeyError(f"trigger state not found: {key}")
    if row.get("last_trigger_key") != task.get("trigger_key"):
        raise RuntimeError("task trigger_key is not the current trigger state")
    row["research_status"] = status
    row["updated_at"] = _now()
    state["updated_at"] = _now()
    _write_json(state_path, state)


def create_path_research_chat_task(
    *,
    root: Path,
    data_root: Path,
    task_id: str,
    writeback_mode: str = "FORMAL",
    full_runtime_run_id: str | None = None,
) -> dict[str, Any]:
    source_task = _read_json(data_root / "research_tasks" / f"{task_id}.json")
    chat_root = data_root / "chat_tasks"
    chat_root.mkdir(parents=True, exist_ok=True)

    for existing_path in sorted(chat_root.glob("*/chat_task.json")):
        try:
            existing = _read_json(existing_path)
        except Exception:
            continue
        if (
            existing.get("task_type") == "PATH_RESEARCH"
            and existing.get("source_research_task_id") == task_id
            and existing.get("status") in {"WAITING_FOR_CHAT", "CLAIMED_BY_CHAT"}
        ):
            if full_runtime_run_id and not existing.get("full_runtime_run_id"):
                existing["full_runtime_run_id"] = full_runtime_run_id
                existing["resume_endpoint"] = (
                    f"/api/opportunity/full-runs/{full_runtime_run_id}/resume-after-chat"
                )
                _write_json(existing_path, existing)
            return existing

    descriptor = prepare_path_research_ai_input(task_id, data_root)
    work_dir = Path(descriptor["work_dir"])
    input_path = Path(descriptor["input_path"])
    input_payload = _read_json(input_path)

    chat_task_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_path_chat_"
        + uuid.uuid4().hex[:8]
    )
    chat_dir = chat_root / chat_task_id
    chat_dir.mkdir(parents=True, exist_ok=False)
    _write_json(chat_dir / "input.json", input_payload)

    prompt_path = root / "runtime" / "ai_runtime" / "prompts" / "path_research_v1.md"
    if prompt_path.exists():
        (chat_dir / "prompt_snapshot.md").write_text(
            prompt_path.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    tool_ctx = ToolContext(
        business_run_dir=work_dir,
        ai_job_dir=chat_dir,
        input_payload=input_payload,
    )
    prefetched = prefetch_path_research_evidence(tool_ctx, max_docs=3)
    _write_json(
        chat_dir / "engineering_prefetched_evidence.json",
        {"evidence": prefetched},
    )

    task = {
        "task_id": chat_task_id,
        "task_type": "PATH_RESEARCH",
        "execution_mode": "INTERACTIVE_CHAT",
        "status": "WAITING_FOR_CHAT",
        "created_at": _now(),
        "business_run_dir": str(work_dir),
        "source_research_task_id": task_id,
        "bond_code": source_task.get("bond_code"),
        "bond_name": source_task.get("bond_name"),
        "path_id": source_task.get("path_id"),
        "trigger_key": source_task.get("trigger_key"),
        "writeback_mode": writeback_mode,
        "full_runtime_run_id": full_runtime_run_id,
        "resume_endpoint": (
            f"/api/opportunity/full-runs/{full_runtime_run_id}/resume-after-chat"
            if full_runtime_run_id
            else None
        ),
        "expected_output": "path_research_result.json",
        "prefetched_evidence_count": len(prefetched),
        "chat_instruction": (
            "处理 PATH_RESEARCH Chat Task。读取 input.json 与预取证据，"
            "必要时使用 Path Evidence Tools，提交符合 V2 Result Contract 的"
            "结构化结果；Validator PASS 后由 Engineering 决定正式写回。"
        ),
    }
    _write_json(chat_dir / "chat_task.json", task)

    pending_path = data_root / "registry" / "pending_research_tasks.json"
    pending = _read_json(pending_path)
    matched = False
    for item in pending.get("pending_tasks", []):
        if item.get("trigger_key") == source_task.get("trigger_key"):
            item["research_status"] = "WAITING_FOR_CHAT"
            item["chat_task_id"] = chat_task_id
            item["updated_at"] = _now()
            matched = True
            break
    if not matched:
        raise RuntimeError("pending research item not found for chat task")
    pending["updated_at"] = _now()
    _write_json(pending_path, pending)
    set_trigger_research_status(data_root, task_id, "WAITING_FOR_CHAT")
    return task


def run_one_path_research(
    *,
    root: Path,
    data_root: Path,
    task_id: str,
    provider_name: str,
    model: str,
    temperature: float | None = 0.1,
    max_tokens: int | None = 12000,
) -> dict[str, Any]:
    descriptor = prepare_path_research_ai_input(task_id, data_root)
    work_dir = Path(descriptor["work_dir"])
    input_path = Path(descriptor["input_path"])

    set_trigger_research_status(data_root, task_id, "IN_PROGRESS")

    try:
        ai_result = run_ai_job(
            root=root,
            data_root=data_root,
            task_type="PATH_RESEARCH",
            input_file=input_path,
            business_run_dir=work_dir,
            provider_name=provider_name,
            provider_config={},
            model_config={
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
        )

        output = {
            "task_id": task_id,
            "started_at": _now(),
            "ai_job": ai_result,
            "ledger": None,
        }

        if ai_result.get("status") == "PASS":
            ai_job_id = str(ai_result["ai_job_id"])
            result_path = data_root / "ai_jobs" / ai_job_id / "structured_output.json"
            ledger = record_path_research_result(result_path, data_root)
            output["ledger"] = ledger
            output["status"] = "PASS"
        else:
            set_trigger_research_status(data_root, task_id, "PENDING")
            output["status"] = ai_result.get("status") or "FAIL"

        output["completed_at"] = _now()
        _write_json(work_dir / "runner_result.json", output)
        return output

    except Exception as exc:
        set_trigger_research_status(data_root, task_id, "PENDING")
        output = {
            "task_id": task_id,
            "status": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "completed_at": _now(),
        }
        _write_json(work_dir / "runner_result.json", output)
        return output

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]