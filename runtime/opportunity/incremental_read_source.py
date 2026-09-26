"""Controlled opportunity read-source switch with unconditional JSON rollback."""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_storage_cutover import evaluate_cutover_readiness
from runtime.opportunity.incremental_storage_projection import (
    build_candidate_projection,
    build_opportunity_projection,
)

READ_SOURCE_VERSION = "incremental-read-source-controller-v1"
JSON_SOURCE = "JSON_RUNTIME"
SQLITE_SOURCE = "SQLITE_RUNTIME"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")

def current_read_source(data_root: Path) -> str:
    path=data_root/"registry"/"incremental_storage_status.json"
    if not path.exists(): return JSON_SOURCE
    return str(_read_json(path).get("primary_read_source") or JSON_SOURCE)
def _load_pointer_artifact(data_root: Path, pointer_name: str) -> dict[str, Any]:
    pointer=_read_json(data_root/"registry"/pointer_name)
    result_path=Path(str(pointer["result_path"]))
    if not result_path.is_absolute():
        result_path=data_root.parent/result_path
    return _read_json(result_path)

def load_candidate_pool(
    *, data_root: Path, source: str | None = None
) -> dict[str, Any]:
    source=source or current_read_source(data_root)
    if source==JSON_SOURCE:
        return _load_pointer_artifact(data_root,"latest_candidate_pool.json")
    if source==SQLITE_SOURCE:
        payload=build_candidate_projection(
            target_db=data_root/"state"/"incremental_runtime.sqlite"
        )
        return {
            "candidate_pool_version":"candidate-pool-v1",
            "run_id":"sqlite-current-projection",
            "unit":"CANDIDATE_POOL","status":"PASS",
            **payload,
        }
    raise ValueError(f"unsupported read source={source!r}")

def load_opportunity_records(
    *, data_root: Path, source: str | None = None
) -> dict[str, Any]:
    source=source or current_read_source(data_root)
    if source==JSON_SOURCE:
        return _load_pointer_artifact(data_root,"latest_opportunity_records.json")
    if source==SQLITE_SOURCE:
        payload=build_opportunity_projection(
            target_db=data_root/"state"/"incremental_runtime.sqlite"
        )
        return {
            "opportunity_record_version":"opportunity-record-v1",
            "run_id":"sqlite-current-projection",
            "unit":"OPPORTUNITY_RECORD","status":"PASS",
            **payload,
        }
    raise ValueError(f"unsupported read source={source!r}")

def prepare_rollback_manifest(*, data_root: Path) -> dict[str, Any]:
    status_path=data_root/"registry"/"incremental_storage_status.json"
    status=_read_json(status_path)
    manifest={
        "read_source_version":READ_SOURCE_VERSION,
        "prepared_at":_now(),
        "rollback_target":JSON_SOURCE,
        "pre_cutover_primary_read_source":status.get("primary_read_source"),
        "latest_candidate_pool_pointer":_read_json(
            data_root/"registry"/"latest_candidate_pool.json"
        ),
        "latest_opportunity_records_pointer":_read_json(
            data_root/"registry"/"latest_opportunity_records.json"
        ),
    }
    out=data_root/"state"/"read_source_rollback_manifest.json"
    _write_json(out,manifest)
    return manifest
def switch_primary_read_source(
    *, data_root: Path, target: str, reason: str, dry_run: bool = False
) -> dict[str, Any]:
    if target not in {JSON_SOURCE,SQLITE_SOURCE}:
        raise ValueError(f"unsupported target={target!r}")
    status_path=data_root/"registry"/"incremental_storage_status.json"
    status=_read_json(status_path)
    current=str(status.get("primary_read_source") or JSON_SOURCE)
    gate=evaluate_cutover_readiness(data_root=data_root)

    if target==SQLITE_SOURCE and gate.get("status")!="READY":
        return {
            "status":"BLOCKED","current":current,"target":target,
            "reason":"CUTOVER_GATE_NOT_READY","gate":gate,
            "dry_run":dry_run,
        }

    rollback_path=data_root/"state"/"read_source_rollback_manifest.json"
    if target==SQLITE_SOURCE and not rollback_path.exists():
        return {
            "status":"BLOCKED","current":current,"target":target,
            "reason":"ROLLBACK_MANIFEST_MISSING","gate":gate,
            "dry_run":dry_run,
        }

    result={
        "status":"READY" if dry_run else "PASS",
        "current":current,"target":target,"reason":reason,
        "gate":gate,"dry_run":dry_run,"changed_at":_now(),
    }
    if dry_run or current==target:
        return result

    status["primary_read_source"]=target
    status["cutover_state"]=(
        "SQLITE_PRIMARY" if target==SQLITE_SOURCE else "ROLLED_BACK_TO_JSON"
    )
    status["read_source_changed_at"]=result["changed_at"]
    status["read_source_change_reason"]=reason
    _write_json(status_path,status)

    history=data_root/"state"/"read_source_switch_history.jsonl"
    history.parent.mkdir(parents=True,exist_ok=True)
    with history.open("a",encoding="utf-8") as fh:
        fh.write(json.dumps(result,ensure_ascii=False)+"\n")
    return result

def rollback_to_json(*, data_root: Path, reason: str) -> dict[str, Any]:
    # Rollback never depends on SQLite health or the cutover gate.
    return switch_primary_read_source(
        data_root=data_root,target=JSON_SOURCE,reason=reason,dry_run=False
    )

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]