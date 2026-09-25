"""Formal Runtime Unit for ordinary-put Opportunity Discovery."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.contract_facts import fetch_eastmoney_contract_table
from runtime.opportunity.put import judge_market
from runtime.opportunity.put_contract_facts import build_put_contract_facts

PUT_DISCOVERY_VERSION = "put-discovery-runtime-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_put_discovery(
    market_input_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    market_input = json.loads(market_input_path.read_text(encoding="utf-8"))
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_put_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)

    started_at = _now()

    eastmoney = fetch_eastmoney_contract_table()
    eastmoney.to_csv(raw_dir / "eastmoney_cb_contracts.csv", index=False)

    contract_facts = build_put_contract_facts(market_input, eastmoney)
    facts_by_code = {
        str(row["bond_code"]).zfill(6): row for row in contract_facts["rows"]
    }
    availability_by_code = {
        code: row.get("put_mechanism_still_available")
        for code, row in facts_by_code.items()
    }

    judgment = judge_market(
        market_input,
        availability_by_code=availability_by_code,
    )

    result = {
        "run_id": run_id,
        "unit": "PUT_DISCOVERY",
        "runtime_version": PUT_DISCOVERY_VERSION,
        "status": (
            "PASS"
            if judgment["summary"]["INSUFFICIENT_DATA"] == 0
            else "INSUFFICIENT_DATA"
        ),
        "started_at": started_at,
        "completed_at": _now(),
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "contract_facts": contract_facts,
        "judgment": judgment,
    }

    _write_json(run_dir / "put_contract_facts.json", contract_facts)
    _write_json(run_dir / "put_economic_judgment.json", judgment)
    _write_json(run_dir / "put_discovery_result.json", result)
    _write_json(
        run_dir / "run_metadata.json",
        {
            "run_id": run_id,
            "unit": "PUT_DISCOVERY",
            "runtime_version": PUT_DISCOVERY_VERSION,
            "status": result["status"],
            "started_at": started_at,
            "completed_at": result["completed_at"],
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "application_commit_sha": result["application_commit_sha"],
            "knowledge_commit_sha": result["knowledge_commit_sha"],
            "artifacts": {
                "raw_contracts": str(raw_dir / "eastmoney_cb_contracts.csv"),
                "contract_facts": str(run_dir / "put_contract_facts.json"),
                "judgment": str(run_dir / "put_economic_judgment.json"),
                "result": str(run_dir / "put_discovery_result.json"),
            },
        },
    )
    return result
