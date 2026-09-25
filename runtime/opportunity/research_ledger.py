"""Path Research Result validation and Research Ledger V1."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LEDGER_VERSION = "path-research-ledger-v1"
REQUIRED_RESULT_FIELDS = (
    "path_result_id",
    "task_id",
    "trigger_key",
    "bond_code",
    "bond_name",
    "path_id",
    "research_cutoff",
    "review_ready",
    "research_status",
    "fact_spine",
    "judgments",
    "key_evidence",
    "unknown_a",
    "unknown_b",
    "key_risks",
    "failure_conditions",
    "next_update_nodes",
    "economic_status_at_research",
    "economic_judgment_reference",
)
ALLOWED_RESEARCH_STATUS = {"COMPLETED", "NEEDS_EVIDENCE", "UNRESOLVED"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_result(result: dict[str, Any], task: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_RESULT_FIELDS if field not in result]
    if missing:
        raise ValueError(f"Path Result missing required fields: {missing}")

    if result["task_id"] != task["task_id"]:
        raise ValueError("task_id mismatch")
    if result["trigger_key"] != task["trigger_key"]:
        raise ValueError("trigger_key mismatch")
    if str(result["bond_code"]).zfill(6) != str(task["bond_code"]).zfill(6):
        raise ValueError("bond_code mismatch")
    if result["path_id"] != task["path_id"]:
        raise ValueError("path_id mismatch")
    if result["economic_status_at_research"] != "KEEP":
        raise ValueError("Path Research Result must preserve Economic KEEP")
    if result["research_status"] not in ALLOWED_RESEARCH_STATUS:
        raise ValueError("invalid research_status")
    if result["review_ready"] and result["research_status"] != "COMPLETED":
        raise ValueError("review_ready=true requires research_status=COMPLETED")
    if result["review_ready"] and result.get("unknown_b"):
        raise ValueError("review_ready=true cannot contain material UNKNOWN-B")


def record_path_research_result(
    result_path: Path,
    data_root: Path,
) -> dict[str, Any]:
    result = _read_json(result_path)
    task_path = data_root / "research_tasks" / f"{result['task_id']}.json"
    if not task_path.exists():
        raise FileNotFoundError(f"task package not found: {task_path}")
    task = _read_json(task_path)
    _validate_result(result, task)

    canonical_result_path = data_root / "path_results" / f"{result['path_result_id']}.json"
    _write_json(canonical_result_path, result)

    ledger_path = data_root / "registry" / "research_ledger.json"
    ledger = _read_json(ledger_path) if ledger_path.exists() else {
        "ledger_version": LEDGER_VERSION,
        "results": {},
    }
    ledger.setdefault("results", {})[result["trigger_key"]] = {
        "trigger_key": result["trigger_key"],
        "task_id": result["task_id"],
        "path_result_id": result["path_result_id"],
        "bond_code": str(result["bond_code"]).zfill(6),
        "bond_name": result["bond_name"],
        "path_id": result["path_id"],
        "research_status": result["research_status"],
        "review_ready": bool(result["review_ready"]),
        "completed_at": _now(),
        "result_path": str(canonical_result_path),
    }
    ledger["ledger_version"] = LEDGER_VERSION
    ledger["updated_at"] = _now()
    _write_json(ledger_path, ledger)

    state_path = data_root / "registry" / "research_trigger_state.json"
    state = _read_json(state_path)
    state_key = f"{str(result['bond_code']).zfill(6)}:{result['path_id']}"
    if state_key not in state.get("paths", {}):
        raise KeyError(f"trigger state path not found: {state_key}")
    state_row = state["paths"][state_key]
    if state_row.get("last_trigger_key") != result["trigger_key"]:
        raise ValueError("result trigger_key is not current trigger state key")
    state_row["research_status"] = result["research_status"]
    state_row["last_path_result_id"] = result["path_result_id"]
    state_row["updated_at"] = _now()
    state["updated_at"] = _now()
    _write_json(state_path, state)

    pending_path = data_root / "registry" / "pending_research_tasks.json"
    pending = _read_json(pending_path)
    pending["pending_tasks"] = [
        item for item in pending.get("pending_tasks", [])
        if item.get("trigger_key") != result["trigger_key"]
    ]
    pending["updated_at"] = _now()
    _write_json(pending_path, pending)

    latest_path = data_root / "registry" / "latest_path_research_result.json"
    _write_json(latest_path, {
        "path_result_id": result["path_result_id"],
        "task_id": result["task_id"],
        "trigger_key": result["trigger_key"],
        "bond_code": str(result["bond_code"]).zfill(6),
        "path_id": result["path_id"],
        "research_status": result["research_status"],
        "review_ready": bool(result["review_ready"]),
        "result_path": str(canonical_result_path),
    })

    return {
        "status": "PASS",
        "path_result_id": result["path_result_id"],
        "research_status": result["research_status"],
        "review_ready": bool(result["review_ready"]),
        "result_path": str(canonical_result_path),
        "remaining_pending": len(pending["pending_tasks"]),
    }
