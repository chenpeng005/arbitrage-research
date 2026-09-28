"""Formal Runtime Unit for Downward Revision Opportunity Discovery."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.opportunity.downward_revision import judge_market
from runtime.opportunity.revision_contract_facts import (
    build_revision_contract_facts,
    fetch_latest_audited_nav,
    fetch_revision_watch,
    load_nav_clause_evidence,
)

REVISION_DISCOVERY_VERSION = "downward-revision-discovery-runtime-v1"
DEFAULT_NAV_EVIDENCE = Path("reference") / "revision_nav_clause_evidence_v1.json"
BUNDLED_NAV_EVIDENCE = Path(__file__).resolve().parents[1] / "reference" / "revision_nav_clause_evidence_v1.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_nav_evidence_layers(
    data_root: Path,
    explicit_path: Path | None = None,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    merged: dict[str, dict[str, Any]] = {}
    loaded_paths: list[str] = []

    candidates: list[Path] = [BUNDLED_NAV_EVIDENCE]
    server_path = data_root / DEFAULT_NAV_EVIDENCE
    if server_path not in candidates:
        candidates.append(server_path)
    if explicit_path is not None and explicit_path not in candidates:
        candidates.append(explicit_path)

    for candidate in candidates:
        if not candidate.exists():
            continue
        merged.update(load_nav_clause_evidence(candidate))
        loaded_paths.append(str(candidate))

    return merged, loaded_paths


def run_downward_revision_discovery(
    market_input_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
    *,
    nav_evidence_path: Path | None = None,
) -> dict[str, Any]:
    market_input = json.loads(market_input_path.read_text(encoding="utf-8"))
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_revision_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    started_at = _now()

    nav_evidence, nav_evidence_paths = _load_nav_evidence_layers(
        data_root,
        explicit_path=nav_evidence_path,
    )

    revision_watch = fetch_revision_watch()
    nav_frame = fetch_latest_audited_nav(market_input["market_cutoff"])
    revision_watch.to_csv(raw_dir / "kzzdata_revision_watch.csv", index=False)
    nav_frame.to_csv(raw_dir / "audited_nav_screen.csv", index=False)

    contract_facts = build_revision_contract_facts(
        market_input,
        revision_watch,
        nav_frame,
        nav_clause_evidence=nav_evidence,
    )

    manifest_path = Path(market_input["market_contract_manifest"])
    judgment = judge_market(
        market_input,
        contract_facts,
        manifest_path,
    )

    status = (
        "PASS"
        if judgment["summary"]["INSUFFICIENT_DATA"] == 0
        else "INSUFFICIENT_DATA"
    )
    decision_sensitive_nav = [
        row for row in judgment["rows"]
        if row.get("nav_floor_decision_sensitive") is True
    ]

    result = {
        "run_id": run_id,
        "unit": "DOWNWARD_REVISION_DISCOVERY",
        "runtime_version": REVISION_DISCOVERY_VERSION,
        "status": status,
        "started_at": started_at,
        "completed_at": _now(),
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "nav_evidence_path": nav_evidence_paths[-1] if nav_evidence_paths else None,
        "nav_evidence_paths": nav_evidence_paths,
        "contract_facts_audit": contract_facts["audit"],
        "decision_sensitive_nav_count": len(decision_sensitive_nav),
        "judgment": judgment,
    }

    _write_json(run_dir / "revision_contract_facts.json", contract_facts)
    _write_json(run_dir / "revision_economic_judgment.json", judgment)
    _write_json(run_dir / "revision_discovery_result.json", result)

    registry_pointer = data_root / "registry" / "latest_revision_discovery.json"
    _write_json(
        registry_pointer,
        {
            "run_id": run_id,
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "status": status,
            "result_path": str(run_dir / "revision_discovery_result.json"),
        },
    )

    _write_json(
        run_dir / "run_metadata.json",
        {
            "run_id": run_id,
            "unit": "DOWNWARD_REVISION_DISCOVERY",
            "runtime_version": REVISION_DISCOVERY_VERSION,
            "status": status,
            "started_at": started_at,
            "completed_at": result["completed_at"],
            "market_run_id": result["market_run_id"],
            "market_snapshot_id": result["market_snapshot_id"],
            "market_cutoff": result["market_cutoff"],
            "application_commit_sha": result["application_commit_sha"],
            "knowledge_commit_sha": result["knowledge_commit_sha"],
            "nav_evidence_path": result["nav_evidence_path"],
            "nav_evidence_paths": result["nav_evidence_paths"],
            "artifacts": {
                "raw_revision_watch": str(raw_dir / "kzzdata_revision_watch.csv"),
                "raw_nav_screen": str(raw_dir / "audited_nav_screen.csv"),
                "contract_facts": str(run_dir / "revision_contract_facts.json"),
                "judgment": str(run_dir / "revision_economic_judgment.json"),
                "result": str(run_dir / "revision_discovery_result.json"),
            },
        },
    )
    return result
