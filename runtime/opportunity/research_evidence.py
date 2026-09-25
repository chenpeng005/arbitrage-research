"""Build Path Research Evidence Packs from structured sources."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from runtime.opportunity.evidence_sources import (
    compact_financial_fact,
    fetch_bulk_financial,
    fetch_revision_notice_index,
    fetch_structured_rating,
    statement_date_for_cutoff,
)

EVIDENCE_PACK_VERSION = "path-research-evidence-pack-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["bond_code"]).zfill(6): row for row in rows}


def _begin_date(cutoff: str) -> str:
    year = pd.Timestamp(cutoff).year
    return f"{year - 1}0101"


def build_research_evidence(
    task_batch_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    batch = _read_json(task_batch_path)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_evidence_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=False)
    evidence_store = data_root / "research_evidence"
    evidence_store.mkdir(parents=True, exist_ok=True)

    registry_pointer = _read_json(
        data_root / "registry" / "latest_economic_path_registry.json"
    )
    registry = _read_json(Path(registry_pointer["result_path"]))
    if registry["run_id"] != batch["economic_registry_run_id"]:
        raise RuntimeError("task batch and economic registry run mismatch")

    market_input = _read_json(
        data_root
        / "runs"
        / registry["market_run_id"]
        / "discovery_market_input.json"
    )
    market_rows = _index(market_input["rows"])

    task_packages = [
        _read_json(Path(item["task_path"]))
        for item in batch["tasks"]
    ]
    maturity_tasks = [x for x in task_packages if x["path_id"] == "MATURITY_CASH"]
    revision_tasks = [x for x in task_packages if x["path_id"] == "DOWNWARD_REVISION"]

    statement_date = statement_date_for_cutoff(batch["market_cutoff"])
    balance = pd.DataFrame()
    cashflow = pd.DataFrame()
    balance_index: dict[str, dict[str, Any]] = {}
    cashflow_index: dict[str, dict[str, Any]] = {}

    if maturity_tasks:
        balance, cashflow = fetch_bulk_financial(statement_date)
        balance.to_csv(raw_dir / "bulk_balance_sheet.csv", index=False)
        cashflow.to_csv(raw_dir / "bulk_cash_flow.csv", index=False)
        balance_index = balance.drop_duplicates("股票代码").set_index(
            "股票代码"
        ).to_dict("index")
        cashflow_index = cashflow.drop_duplicates("股票代码").set_index(
            "股票代码"
        ).to_dict("index")

    rating_rows = []
    revision_notice_matched = 0
    packs = []

    for task in task_packages:
        code = str(task["bond_code"]).zfill(6)
        market_row = market_rows[code]
        stock_code = str(market_row["stock_code"]).zfill(6)
        path_id = task["path_id"]
        facts: dict[str, Any] = {}
        sources: list[dict[str, Any]] = []
        missing_or_deferred: list[str] = []
        coverage: dict[str, Any] = {}

        if path_id == "MATURITY_CASH":
            financial = compact_financial_fact(
                stock_code,
                statement_date,
                balance_index,
                cashflow_index,
            )
            rating = fetch_structured_rating(code)
            rating_rows.append({
                "bond_code": code,
                "bond_name": task["bond_name"],
                "stock_code": stock_code,
                **rating,
            })

            remaining_size = float(task["market_state"]["remaining_size"])
            judgment = task["economic_judgment"]
            unit_cash = float(
                judgment.get("remaining_contract_cash_C_max")
                or judgment["remaining_contract_cash_C"]
            )
            responsibility = remaining_size * unit_cash / 100.0

            facts = {
                "max_contract_cash_responsibility_yi": responsibility,
                "remaining_size_yi": remaining_size,
                "unit_remaining_contract_cash": unit_cash,
                "financial_first_layer": financial,
                "structured_rating": rating,
            }
            sources = [
                {
                    "source_id": "BULK_FINANCIALS",
                    "source": financial["source"],
                    "statement_date": statement_date,
                    "scope": "CONSOLIDATED",
                },
                {
                    "source_id": "STRUCTURED_RATING",
                    "source": rating["source"],
                    "scope": "BOND",
                },
            ]
            coverage = {
                "financial_matched": (
                    financial["balance_sheet_matched"]
                    and financial["cash_flow_matched"]
                ),
                "rating_matched": bool(rating["matched"]),
            }
            missing_or_deferred = [
                "parent_entity_cash_and_cashflow",
                "latest_rating_report_semantic_review",
                "hard_credit_event_scan",
                "financing_support_semantic_review",
            ]

        elif path_id == "DOWNWARD_REVISION":
            frame, notices = fetch_revision_notice_index(
                stock_code,
                _begin_date(batch["market_cutoff"]),
                batch["market_cutoff"].replace("-", ""),
            )
            safe_code = code.replace("/", "_")
            frame.to_csv(
                raw_dir / f"revision_notices_{safe_code}.csv",
                index=False,
            )
            if notices:
                revision_notice_matched += 1

            facts = {
                "existing_contract_fact": task["existing_path_facts"].get(
                    "contract_fact"
                ),
                "official_notice_behavior_index": notices,
            }
            sources = [{
                "source_id": "REVISION_NOTICE_INDEX",
                "source": "AKShare/Eastmoney official-announcement index",
                "begin_date": _begin_date(batch["market_cutoff"]),
                "end_date": batch["market_cutoff"],
                "stock_code": stock_code,
            }]
            coverage = {
                "revision_notice_index_matched": bool(notices),
                "revision_notice_count": len(notices),
            }
            missing_or_deferred = [
                "primary_document_semantic_read_for_decision_sensitive_events",
                "issuer_objective_interpretation",
                "governance_bottleneck_interpretation",
                "realistic_revision_depth_judgment",
            ]

        else:
            raise ValueError(f"unsupported path_id: {path_id}")

        pack = {
            "evidence_pack_version": EVIDENCE_PACK_VERSION,
            "task_id": task["task_id"],
            "trigger_key": task["trigger_key"],
            "bond_code": code,
            "bond_name": task["bond_name"],
            "stock_code": stock_code,
            "path_id": path_id,
            "market_cutoff": batch["market_cutoff"],
            "research_task_path": str(
                data_root / "research_tasks" / f"{task['task_id']}.json"
            ),
            "created_at": _now(),
            "application_commit_sha": deployment.get("application_commit_sha"),
            "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
            "sources": sources,
            "facts": facts,
            "coverage": coverage,
            "missing_or_deferred": missing_or_deferred,
            "engineering_rule": (
                "Evidence pack contains preloaded facts only; "
                "AI Path Research remains responsible for semantic judgment."
            ),
        }
        pack_path = evidence_store / f"{task['task_id']}.json"
        _write_json(pack_path, pack)
        packs.append({
            "task_id": task["task_id"],
            "bond_code": code,
            "bond_name": task["bond_name"],
            "path_id": path_id,
            "evidence_pack_path": str(pack_path),
            "coverage": coverage,
        })

    if rating_rows:
        pd.DataFrame(rating_rows).to_csv(
            raw_dir / "structured_ratings.csv",
            index=False,
        )

    maturity_financial_matched = sum(
        bool(x["coverage"].get("financial_matched"))
        for x in packs if x["path_id"] == "MATURITY_CASH"
    )
    maturity_rating_matched = sum(
        bool(x["coverage"].get("rating_matched"))
        for x in packs if x["path_id"] == "MATURITY_CASH"
    )

    result = {
        "run_id": run_id,
        "unit": "PATH_RESEARCH_EVIDENCE_INGRESS",
        "evidence_pack_version": EVIDENCE_PACK_VERSION,
        "status": "PASS",
        "created_at": _now(),
        "task_batch_run_id": batch["run_id"],
        "economic_registry_run_id": batch["economic_registry_run_id"],
        "market_snapshot_id": batch["market_snapshot_id"],
        "market_cutoff": batch["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "tasks": len(task_packages),
        "packs_built": len(packs),
        "maturity_tasks": len(maturity_tasks),
        "maturity_financial_matched": maturity_financial_matched,
        "maturity_rating_matched": maturity_rating_matched,
        "revision_tasks": len(revision_tasks),
        "revision_notice_index_matched": revision_notice_matched,
        "packs": packs,
    }

    _write_json(run_dir / "path_research_evidence_batch.json", result)
    _write_json(run_dir / "run_metadata.json", {
        "run_id": run_id,
        "unit": "PATH_RESEARCH_EVIDENCE_INGRESS",
        "evidence_pack_version": EVIDENCE_PACK_VERSION,
        "status": "PASS",
        "created_at": result["created_at"],
        "task_batch_run_id": batch["run_id"],
        "market_snapshot_id": batch["market_snapshot_id"],
        "application_commit_sha": result["application_commit_sha"],
        "knowledge_commit_sha": result["knowledge_commit_sha"],
        "artifacts": {
            "evidence_batch": str(run_dir / "path_research_evidence_batch.json"),
            "raw_dir": str(raw_dir),
            "evidence_store": str(evidence_store),
        },
    })

    latest = data_root / "registry" / "latest_path_research_evidence.json"
    _write_json(latest, {
        "run_id": run_id,
        "task_batch_run_id": batch["run_id"],
        "market_snapshot_id": batch["market_snapshot_id"],
        "status": "PASS",
        "tasks": len(task_packages),
        "packs_built": len(packs),
        "result_path": str(run_dir / "path_research_evidence_batch.json"),
        "evidence_store": str(evidence_store),
    })
    return result
