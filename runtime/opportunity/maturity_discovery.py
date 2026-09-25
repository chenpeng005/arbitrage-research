"""Formal Runtime Unit for maturity-cash Opportunity Discovery."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.contract_facts import (
    build_maturity_contract_facts,
    fetch_eastmoney_contract_table,
    fetch_ths_contract_maturity,
    load_overrides,
)
from runtime.opportunity.maturity_cash import judge_market
from runtime.opportunity.maturity_path_availability import (
    build_maturity_path_availability,
    fetch_redeem_status,
)

MATURITY_DISCOVERY_VERSION = "maturity-cash-discovery-runtime-v1"
DEFAULT_OVERRIDE_RELATIVE_PATH = Path("reference") / "maturity_cash_overrides_v1.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_maturity_discovery(
    market_input_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
    *,
    overrides_path: Path | None = None,
) -> dict[str, Any]:
    market_input = json.loads(market_input_path.read_text(encoding="utf-8"))
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_maturity_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    started_at = _now()

    if overrides_path is None:
        candidate = data_root / DEFAULT_OVERRIDE_RELATIVE_PATH
        overrides_path = candidate if candidate.exists() else None

    eastmoney = fetch_eastmoney_contract_table()
    ths = fetch_ths_contract_maturity()
    eastmoney.to_csv(raw_dir / "eastmoney_cb_contracts.csv", index=False)
    ths.to_csv(raw_dir / "ths_cb_maturity.csv", index=False)

    overrides = load_overrides(overrides_path)
    contract_facts = build_maturity_contract_facts(
        market_input,
        eastmoney,
        ths,
        overrides=overrides,
    )

    preliminary = judge_market(market_input, contract_facts)
    candidates = [
        row for row in preliminary["rows"]
        if row.get("reason") == "NORMAL_MATURITY_PATH_AVAILABILITY_REQUIRED"
    ]

    if candidates:
        redeem_status = fetch_redeem_status()
        redeem_status.to_csv(raw_dir / "jisilu_redeem_status.csv", index=False)
        availability = build_maturity_path_availability(
            candidates,
            contract_facts,
            redeem_status,
        )
    else:
        availability = {
            "availability_version": "maturity-path-availability-v1",
            "created_at": _now(),
            "candidate_count": 0,
            "rows": [],
            "audit": {"ready": 0, "insufficient_data": 0},
        }

    availability_by_code = {
        str(row["bond_code"]).zfill(6): row.get("normal_maturity_path_available")
        for row in availability["rows"]
    }
    judgment = judge_market(
        market_input,
        contract_facts,
        availability_by_code=availability_by_code,
    )

    status = (
        "PASS"
        if judgment["summary"]["INSUFFICIENT_DATA"] == 0
        else "INSUFFICIENT_DATA"
    )
    result = {
        "run_id": run_id,
        "unit": "MATURITY_CASH_DISCOVERY",
        "runtime_version": MATURITY_DISCOVERY_VERSION,
        "status": status,
        "started_at": started_at,
        "completed_at": _now(),
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "overrides_path": str(overrides_path) if overrides_path else None,
        "contract_facts_audit": contract_facts["audit"],
        "preliminary_summary": preliminary["summary"],
        "availability_audit": availability["audit"],
        "judgment": judgment,
    }

    _write_json(run_dir / "maturity_contract_facts.json", contract_facts)
    _write_json(run_dir / "maturity_preliminary_judgment.json", preliminary)
    _write_json(run_dir / "maturity_path_availability.json", availability)
    _write_json(run_dir / "maturity_economic_judgment.json", judgment)
    _write_json(run_dir / "maturity_discovery_result.json", result)

    registry_pointer = data_root / "registry" / "latest_maturity_discovery.json"
    _write_json(
        registry_pointer,
        {
            "run_id": run_id,
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "status": status,
            "result_path": str(run_dir / "maturity_discovery_result.json"),
        },
    )

    _write_json(
        run_dir / "run_metadata.json",
        {
            "run_id": run_id,
            "unit": "MATURITY_CASH_DISCOVERY",
            "runtime_version": MATURITY_DISCOVERY_VERSION,
            "status": status,
            "started_at": started_at,
            "completed_at": result["completed_at"],
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "application_commit_sha": result["application_commit_sha"],
            "knowledge_commit_sha": result["knowledge_commit_sha"],
            "overrides_path": result["overrides_path"],
            "artifacts": {
                "raw_contracts": str(raw_dir / "eastmoney_cb_contracts.csv"),
                "raw_maturity_crosscheck": str(raw_dir / "ths_cb_maturity.csv"),
                "raw_redeem_status": (
                    str(raw_dir / "jisilu_redeem_status.csv") if candidates else None
                ),
                "contract_facts": str(run_dir / "maturity_contract_facts.json"),
                "preliminary_judgment": str(run_dir / "maturity_preliminary_judgment.json"),
                "path_availability": str(run_dir / "maturity_path_availability.json"),
                "judgment": str(run_dir / "maturity_economic_judgment.json"),
                "result": str(run_dir / "maturity_discovery_result.json"),
            },
        },
    )
    return result
