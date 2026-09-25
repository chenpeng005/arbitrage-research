"""Unified Opportunity Discovery Controller / Economic Path Registry V1.

The controller fixes one Discovery Market Ingress and invokes each active Path
Runtime independently. Economic statuses remain Path-local and are aggregated
under the bond only for registry / delivery convenience.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.maturity_discovery import run_maturity_discovery
from runtime.opportunity.put_discovery import run_put_discovery
from runtime.opportunity.revision_discovery import run_downward_revision_discovery

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
    revision = run_downward_revision_discovery(
        market_input_path,
        data_root,
        deployment,
    )

    child_runs = {
        "MATURITY_CASH": maturity,
        "PUT": put,
        "DOWNWARD_REVISION": revision,
    }
    child_rows = {
        path_id: _index(child["judgment"]["rows"])
        for path_id, child in child_runs.items()
    }

    bonds = []
    for market_row in market_input["rows"]:
        code = str(market_row["bond_code"]).zfill(6)
        paths = {}
        for path_id in ACTIVE_PATHS:
            paths[path_id] = {
                "runtime_status": "CONNECTED",
                **child_rows[path_id][code],
            }

        bonds.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            "market_snapshot_id": market_input["market_snapshot_id"],
            "market_cutoff": market_input["market_cutoff"],
            "paths": paths,
        })

    path_summary = {}
    for path_id, child in child_runs.items():
        path_summary[path_id] = {
            "runtime_status": "CONNECTED",
            "child_run_id": child["run_id"],
            "child_status": child["status"],
            **child["judgment"]["summary"],
        }

    all_pass = all(child["status"] == "PASS" for child in child_runs.values())
    status = "PASS" if all_pass else "INSUFFICIENT_DATA"

    any_keep = sum(
        any(path.get("economic_status") == "KEEP" for path in bond["paths"].values())
        for bond in bonds
    )

    result = {
        "run_id": run_id,
        "unit": "OPPORTUNITY_DISCOVERY_CONTROLLER",
        "controller_version": CONTROLLER_VERSION,
        "status": status,
        "started_at": started_at,
        "completed_at": _now(),
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "active_paths": list(ACTIVE_PATHS),
        "connected_paths": list(ACTIVE_PATHS),
        "not_connected_paths": [],
        "path_summary": path_summary,
        "bonds_with_any_keep": any_keep,
        "bonds": bonds,
    }

    _write_json(run_dir / "economic_path_registry.json", result)
    _write_json(
        run_dir / "run_metadata.json",
        {
            "run_id": run_id,
            "unit": "OPPORTUNITY_DISCOVERY_CONTROLLER",
            "controller_version": CONTROLLER_VERSION,
            "status": status,
            "started_at": started_at,
            "completed_at": result["completed_at"],
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "application_commit_sha": result["application_commit_sha"],
            "knowledge_commit_sha": result["knowledge_commit_sha"],
            "child_runs": {
                path_id: child["run_id"]
                for path_id, child in child_runs.items()
            },
            "artifacts": {
                "economic_path_registry": str(
                    run_dir / "economic_path_registry.json"
                ),
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
            "status": status,
            "connected_paths": result["connected_paths"],
            "not_connected_paths": result["not_connected_paths"],
            "bonds_with_any_keep": any_keep,
            "result_path": str(run_dir / "economic_path_registry.json"),
        },
    )
    return result
