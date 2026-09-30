"""Checkpointed daily notice-lane runner.

The latest scanned day is intentionally re-scanned on every run. Database
idempotency prevents duplicates and catches late same-day disclosures.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.incremental_information_runtime import run_information_lane

CHECKPOINT_VERSION = "incremental-notice-checkpoint-v1"

def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def _parse_day(value: str) -> date:
    return datetime.strptime(value,"%Y-%m-%d").date()

def _days(start: date, end: date):
    current=start
    while current<=end:
        yield current
        current += timedelta(days=1)

def run_notice_checkpoint(
    *,
    data_root: Path,
    target_date: str,
) -> dict[str, Any]:
    status_path=data_root/"registry"/"incremental_storage_status.json"
    db_path=data_root/"state"/"incremental_runtime.sqlite"
    if not status_path.exists() or not db_path.exists():
        raise RuntimeError("incremental storage is not initialized")

    status=_read_json(status_path)
    activation=_parse_day(str(status["notice_lane_activation_date"]))
    last_text=str(status.get("last_successful_notice_scan_date") or activation.isoformat())
    last=_parse_day(last_text)
    target=_parse_day(target_date)

    # Never scan before activation. Re-scan the latest successful day to catch
    # late disclosures published later on the same date.
    start=max(activation,last)
    if target < start:
        return {
            "checkpoint_version":CHECKPOINT_VERSION,
            "status":"SKIPPED",
            "reason":"TARGET_BEFORE_SCAN_START",
            "activation_date":activation.isoformat(),
            "last_successful_notice_scan_date":last.isoformat(),
            "target_date":target.isoformat(),
            "days":[],
        }

    results=[]
    overall="PASS"
    for day in _days(start,target):
        try:
            result=run_information_lane(
                data_root=data_root,
                scan_date=day.strftime("%Y%m%d"),
                target_db=db_path,
            )
            results.append({
                "notice_date":day.isoformat(),
                "status":"PASS",
                "information_run_id":result["run_id"],
                "all_notice_count":result["scan"]["all_notice_count"],
                "relevant_notice_count":result["scan"][
                    "relevant_document_count"
                ],
                "stats":result["ledger"],
            })
        except Exception as exc:
            results.append({
                "notice_date":day.isoformat(),
                "status":"FAIL",
                "error":f"{type(exc).__name__}: {exc}",
            })
            overall="FAIL"
            break

    successful=[x["notice_date"] for x in results if x["status"]=="PASS"]
    if successful:
        status["last_successful_notice_scan_date"]=successful[-1]
    status["last_notice_checkpoint_run_at"]=datetime.now(timezone.utc).isoformat()
    status["last_notice_checkpoint_status"]=overall
    status["notice_lane_status"]="ACTIVE"
    _write_json(status_path,status)

    output={
        "checkpoint_version":CHECKPOINT_VERSION,
        "status":overall,
        "activation_date":activation.isoformat(),
        "scan_start_date":start.isoformat(),
        "target_date":target.isoformat(),
        "last_successful_notice_scan_date":status.get("last_successful_notice_scan_date"),
        "days":results,
    }
    _write_json(data_root/"registry"/"latest_notice_checkpoint.json",output)
    return output

