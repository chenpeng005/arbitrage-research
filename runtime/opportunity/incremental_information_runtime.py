"""Independent Information Lane Runtime.

Unlike the market lane, a same-date rerun is allowed because late announcements
may appear. Persistence is idempotent; reruns only add genuinely new evidence.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from runtime.opportunity.incremental_event_ledger import persist_information_scan
from runtime.opportunity.incremental_information_scan import (
    scan_daily_relevant_notices,
)

INFORMATION_RUNTIME_VERSION = "incremental-information-runtime-v1"

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()

def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

def run_information_lane(
    *,
    data_root: Path,
    scan_date: str,
    target_db: Path | None = None,
    fetcher: Callable[..., pd.DataFrame] | None = None,
) -> dict[str, Any]:
    target_db = target_db or data_root / "state" / "incremental_runtime.sqlite"
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_information_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "information_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    status_path = run_dir / "status.json"
    state = {
        "information_runtime_version": INFORMATION_RUNTIME_VERSION,
        "run_id": run_id,
        "scan_date": scan_date,
        "status": "RUNNING",
        "started_at": _now(),
        "database_path": str(target_db),
    }
    _write_json(status_path, state)
    try:
        scan = scan_daily_relevant_notices(
            target_db=target_db,
            date=scan_date,
            fetcher=fetcher,
        )
        scan_path = run_dir / "information_scan.json"
        _write_json(scan_path, scan)
        state["scan"] = {
            "all_notice_count": scan.get("all_notice_count"),
            "relevant_document_count": scan.get("relevant_document_count"),
            "affected_bond_count": scan.get("affected_bond_count"),
            "event_kind_summary": scan.get("event_kind_summary"),
        }
        state["artifacts"] = {"scan": str(scan_path)}
        _write_json(status_path, state)

        ledger = persist_information_scan(
            target_db=target_db,
            scan=scan,
        )
        ledger_path = run_dir / "event_ledger_result.json"
        _write_json(ledger_path, ledger)
        state["artifacts"]["event_ledger_result"] = str(ledger_path)
        state["ledger"] = ledger.get("stats", {})
        state["status"] = "PASS"
        state["completed_at"] = _now()
        _write_json(status_path, state)

        registry_dir = data_root / "registry" / "information_lane"
        _write_json(
            registry_dir / f"{scan_date}.json",
            {
                "scan_date": scan_date,
                "last_run_id": run_id,
                "status": "PASS",
                "status_path": str(status_path),
                "scan_path": str(scan_path),
                "event_ledger_result_path": str(ledger_path),
                "completed_at": state["completed_at"],
                "relevant_document_count": scan.get("relevant_document_count"),
                "new_evidence_count": ledger.get("stats", {}).get(
                    "evidence_documents_inserted"
                ),
                "new_event_count": (
                    int(ledger.get("stats", {}).get("confirmed_events_inserted") or 0)
                    + int(ledger.get("stats", {}).get("semantic_candidates_inserted") or 0)
                ),
            },
        )
        _write_json(
            data_root / "registry" / "latest_information_lane.json",
            {
                "scan_date": scan_date,
                "run_id": run_id,
                "status": "PASS",
                "status_path": str(status_path),
                "completed_at": state["completed_at"],
            },
        )
        return state
    except Exception as exc:
        state["status"] = "FAIL"
        state["completed_at"] = _now()
        state["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(status_path, state)
        _write_json(
            data_root / "registry" / "latest_information_lane.json",
            {
                "scan_date": scan_date,
                "run_id": run_id,
                "status": "FAIL",
                "status_path": str(status_path),
                "completed_at": state["completed_at"],
                "error": state["error"],
            },
        )
        raise

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]