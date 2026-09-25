"""Unified Opportunity Discovery Controller / Economic Path Registry V1.

The controller fixes one Discovery Market Ingress and invokes each connected
Path Runtime independently. Unconnected Paths are represented as runtime
coverage gaps, never as economic DROP.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.maturity_discovery import run_maturity_discovery
from runtime.opportunity.put_discovery import run_put_discovery

CONTROLLER_VERSION = "opportunity-discovery-controller-v1"
ACTIVE_PATHS = ("MATURITY_CASH", "PUT", "DOWNWARD_REVISION")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["bond_code"]).zfill(6): row for row in rows}


def run_opportunity_discovery(
    market_input_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    market_input = json.loads(market_input_path.read_text(encoding="utf-8"))
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_discovery_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    started_at = _now()

    maturity = run_maturity_discovery(market_input_path, data_root, deployment)
    put = run_put_discovery(market_input_path, data_root, deployment)

    maturity_rows = _index(maturity["judgment"]["rows"])
    put_rows = _index(put["judgment"]["rows"])

    bonds = []
    for market_row in market_input["rows"]:
        code = str(market_row["bond_code"]).zfill(6)
        maturity_row = maturity_rows[code]
        put_row = put_rows[code]

        bonds.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            "market_snapshot_id": market_input["market_snapshot_id"],
            "market_cutoff": market_input["market_cutoff"],
            "paths": {
                "MATURITY_CASH": {
                    "runtime_status": "CONNECTED",
                    **maturity_row,
                },
                "PUT": {
                    "runtime_status": "CONNECTED",
                    **put_row,
                },
                "DOWNWARD_REVISION": {
                    "runtime_status": "NOT_CONNECTED",
                    "economic_status": None,
                    "reason": "DOWNWARD_REVISION_RUNTIME_NOT_CONNECTED",
                },
            },
        })

    path_summary = {
        "MATURITY_CASH": {
            "runtime_status": "CONNECTED",
            "child_run_id": maturity["run_id"],
            **maturity["judgment"]["summary"],
        },
        "PUT": {
            "runtime_status": "CONNECTED",
            "child_run_id": put["run_id"],
            **put["judgment"]["summary"],
        },
        "DOWNWARD_REVISION": {
            "runtime_status": "NOT_CONNECTED",
            "KEEP": None,
            "DROP": None,
            "INSUFFICIENT_DATA": None,
        },
    }

    result = {
        "run_id": run_id,
        "unit": "OPPORTUNITY_DISCOVERY_CONTROLLER",
        "controller_version": CONTROLLER_VERSION,
        "status": "PARTIAL_RUNTIME",
        "started_at": started_at,
        "completed_at": _now(),
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "active_paths": list(ACTIVE_PATHS),
        "connected_paths": ["MATURITY_CASH", "PUT"],
        "not_connected_paths": ["DOWNWARD_REVISION"],
        "path_summary": path_summary,
        "bonds": bonds,
    }

    _write_json(run_dir / "economic_path_registry.json", result)
    _write_json(
        run_dir / "run_metadata.json",
        {
            "run_id": run_id,
            "unit": "OPPORTUNITY_DISCOVERY_CONTROLLER",
            "controller_version": CONTROLLER_VERSION,
            "status": result["status"],
            "started_at": started_at,
            "completed_at": result["completed_at"],
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "application_commit_sha": result["application_commit_sha"],
            "knowledge_commit_sha": result["knowledge_commit_sha"],
            "child_runs": {
                "MATURITY_CASH": maturity["run_id"],
                "PUT": put["run_id"],
            },
            "artifacts": {
                "economic_path_registry": str(run_dir / "economic_path_registry.json"),
            },
        },
    )

    registry_file = data_root / "registry" / "latest_economic_path_registry.json"
    _write_json(
        registry_file,
        {
            "run_id": run_id,
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "status": result["status"],
            "connected_paths": result["connected_paths"],
            "not_connected_paths": result["not_connected_paths"],
            "result_path": str(run_dir / "economic_path_registry.json"),
        },
    )
    return result
