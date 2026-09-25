"""Freeze the audited market portion of the Discovery input.

Contractual facts are deliberately represented as missing until acquired and
audited. A market valuation by itself never creates a Path KEEP/DROP result.
"""

from __future__ import annotations

import json
import math
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from runtime.market_map.resolver import ResolverError, load_contract

INGRESS_VERSION = "discovery-market-ingress-v1"
MARKET_FIELDS = (
    "bond_code", "bond_name", "stock_code",
    "current_bond_price", "current_stock_price", "current_conversion_price",
    "current_conversion_value", "maturity_date", "remaining_months", "remaining_size",
    "model_zone", "data_status", "warning_flags",
)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def build_market_ingress(manifest_path: Path, data_root: Path, deployment: dict, *, expected_snapshot_id: str | None = None) -> dict:
    """Audit a frozen Market Map contract and persist one traceable ingress run."""
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_ingress_" + uuid.uuid4().hex[:8]
    run_dir = data_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    created_at = datetime.now(timezone.utc).isoformat()
    metadata = {
        "run_id": run_id, "unit": "DISCOVERY_MARKET_INGRESS",
        "ingress_version": INGRESS_VERSION, "started_at": created_at,
        "status": "RUNNING", "market_contract_manifest": str(manifest_path),
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
    }
    _write_json(run_dir / "run_metadata.json", metadata)

    try:
        manifest, _, bonds = load_contract(manifest_path, require_formal=True)
        if expected_snapshot_id and manifest.get("snapshot_id") != expected_snapshot_id:
            raise ResolverError("formal registry and market contract snapshot_id mismatch")
        missing = [field for field in MARKET_FIELDS if field not in bonds.columns]
        if missing:
            raise ResolverError(f"market contract missing columns: {missing}")
        if bonds.empty or bonds["bond_code"].duplicated().any():
            raise ResolverError("market contract has no bonds or duplicate bond_code")

        rows = []
        for item in bonds[list(MARKET_FIELDS)].to_dict("records"):
            code = str(item["bond_code"]).zfill(6)
            values = {}
            for key in (
                "current_bond_price", "current_stock_price", "current_conversion_price",
                "current_conversion_value", "remaining_months", "remaining_size",
            ):
                number = float(item[key])
                if not math.isfinite(number) or number <= 0:
                    raise ResolverError(f"invalid {key} for {code}")
                values[key] = number
            rows.append({
                "bond_code": code, "bond_name": str(item["bond_name"]),
                "stock_code": str(item["stock_code"]).zfill(6),
                "maturity_date": str(item["maturity_date"]),
                **values, "model_zone": str(item["model_zone"]),
                "data_status": str(item["data_status"]),
                "warning_flags": "" if pd.isna(item["warning_flags"]) else str(item["warning_flags"]),
                "path_inputs": {
                    "MATURITY_CASH": {"status": "WAITING_CONTRACT_DATA", "missing": ["remaining_contract_cash_C", "normal_maturity_path_available"]},
                    "PUT": {"status": "WAITING_CONTRACT_DATA", "missing": ["put_clause_exists", "put_mechanism_still_available"]},
                    "DOWNWARD_REVISION": {
                        "status": "WAITING_CONTRACT_DATA",
                        "missing": ["revision_clause_available", "permanent_revision_blocker"],
                        "conditional": ["current_hard_floor_if_decision_sensitive"],
                    },
                },
            })

        result = {
            "run_id": run_id, "status": "PASS_MARKET_ONLY", "ingress_version": INGRESS_VERSION,
            "created_at": created_at, "market_cutoff": manifest["market_cutoff"],
            "market_snapshot_id": manifest["snapshot_id"],
            "market_contract_version": manifest["contract_version"],
            "market_model_version": manifest["model_version"],
            "market_contract_manifest": str(manifest_path),
            "audit": {"status": "PASS", "market_rows": len(rows), "invalid_rows": 0, "duplicate_codes": 0},
            "discovery_status": "WAITING_CONTRACT_DATA",
            "rows": rows,
        }
        _write_json(run_dir / "discovery_market_input.json", result)
        metadata.update(status="PASS_MARKET_ONLY", completed_at=datetime.now(timezone.utc).isoformat(),
                        market_cutoff=manifest["market_cutoff"], market_snapshot_id=manifest["snapshot_id"],
                        market_contract_version=manifest["contract_version"],
                        result_path=str(run_dir / "discovery_market_input.json"))
        _write_json(run_dir / "run_metadata.json", metadata)
        return result
    except Exception as exc:
        metadata.update(status="FAIL", completed_at=datetime.now(timezone.utc).isoformat(),
                        error=f"{type(exc).__name__}: {exc}")
        _write_json(run_dir / "run_metadata.json", metadata)
        raise
