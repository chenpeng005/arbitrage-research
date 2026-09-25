"""Orchestrate one PATH_RESEARCH AI job and ledger write-back."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.ai_runtime.engine import run_ai_job
from runtime.opportunity.research_ai_input import prepare_path_research_ai_input
from runtime.opportunity.research_ledger import record_path_research_result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _set_trigger_research_status(
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

    _set_trigger_research_status(data_root, task_id, "IN_PROGRESS")

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
            _set_trigger_research_status(data_root, task_id, "PENDING")
            output["status"] = ai_result.get("status") or "FAIL"

        output["completed_at"] = _now()
        _write_json(work_dir / "runner_result.json", output)
        return output

    except Exception as exc:
        _set_trigger_research_status(data_root, task_id, "PENDING")
        output = {
            "task_id": task_id,
            "status": "FAIL",
            "error": f"{type(exc).__name__}: {exc}",
            "completed_at": _now(),
        }
        _write_json(work_dir / "runner_result.json", output)
        return output
