"""Build deterministic Path Research task packages from the pending queue."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TASK_CONTRACT_VERSION = "path-research-task-contract-v1"

CANONICAL_BY_PATH = {
    "MATURITY_CASH": (
        "05 套利研究/AI-Engineering-Runtime/"
        "05_Path Knowledge/01_到期现金兑付/02_Path-Research.md"
    ),
    "PUT": (
        "05 套利研究/AI-Engineering-Runtime/"
        "05_Path Knowledge/03_回售/03_Path-Research.md"
    ),
    "DOWNWARD_REVISION": (
        "05 套利研究/AI-Engineering-Runtime/"
        "05_Path Knowledge/02_下修/02_Path-Research.md"
    ),
}

RESEARCH_QUESTIONS = {
    "MATURITY_CASH": [
        "确认最大到期现金责任与到期时间轴。",
        "判断当前可支配流动性及到期前现金竞争。",
        "判断内部现金生成与外部缺口闭合来源。",
        "检查硬信用事件与资金来源失效风险。",
        "形成支付稳定性判断、关键失效条件与更新节点。",
    ],
    "PUT": [
        "确认普通回售适用期与最早可能形成权利的时间。",
        "核对当前触发状态、计数及尚需交易日。",
        "分析下修或转股价调整对回售触发线和计数的更新。",
        "估计形成权利后的合同现金与现实回售压力。",
        "复用到期现金框架判断现金支付稳定性并给出更新节点。",
    ],
    "DOWNWARD_REVISION": [
        "确认当前现实事件状态与本轮主要不确定性。",
        "判断发行人当前推进下修的首要目标与持续推进程度。",
        "在当前治理节点下判断最终落地程度。",
        "区分规则允许的修到底边界与发行人现实可能选择的结果。",
        "完成现实结果的经济翻译、主要失效条件与下一更新节点。",
    ],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _task_id(trigger_key: str) -> str:
    digest = hashlib.sha256(trigger_key.encode("utf-8")).hexdigest()[:16]
    return f"research_{digest}"


def _index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["bond_code"]).zfill(6): row for row in rows}


def _load_path_facts(
    data_root: Path,
    registry: dict[str, Any],
) -> dict[str, dict[str, dict[str, Any]]]:
    maturity_run = registry["path_summary"]["MATURITY_CASH"]["child_run_id"]
    put_run = registry["path_summary"]["PUT"]["child_run_id"]
    revision_run = registry["path_summary"]["DOWNWARD_REVISION"]["child_run_id"]

    maturity_contract = _read_json(
        data_root / "runs" / maturity_run / "maturity_contract_facts.json"
    )
    maturity_availability = _read_json(
        data_root / "runs" / maturity_run / "maturity_path_availability.json"
    )
    put_contract = _read_json(
        data_root / "runs" / put_run / "put_contract_facts.json"
    )
    revision_contract = _read_json(
        data_root / "runs" / revision_run / "revision_contract_facts.json"
    )

    return {
        "MATURITY_CASH": {
            "contract": _index(maturity_contract["rows"]),
            "availability": _index(maturity_availability["rows"]),
        },
        "PUT": {
            "contract": _index(put_contract["rows"]),
        },
        "DOWNWARD_REVISION": {
            "contract": _index(revision_contract["rows"]),
        },
    }


def _existing_path_facts(
    path_id: str,
    code: str,
    path_facts: dict[str, dict[str, dict[str, Any]]],
) -> dict[str, Any]:
    if path_id == "MATURITY_CASH":
        return {
            "contract_fact": path_facts[path_id]["contract"].get(code),
            "path_availability": path_facts[path_id]["availability"].get(code),
        }
    if path_id == "PUT":
        return {
            "contract_fact": path_facts[path_id]["contract"].get(code),
        }
    if path_id == "DOWNWARD_REVISION":
        return {
            "contract_fact": path_facts[path_id]["contract"].get(code),
        }
    raise ValueError(f"unsupported path_id: {path_id}")


def build_path_research_tasks(
    economic_registry_path: Path,
    pending_queue_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    registry = _read_json(economic_registry_path)
    pending_queue = _read_json(pending_queue_path)

    if (
        pending_queue.get("economic_registry_run_id")
        and pending_queue["economic_registry_run_id"] != registry["run_id"]
    ):
        raise RuntimeError("pending queue and economic registry run_id mismatch")

    market_input = _read_json(
        data_root
        / "runs"
        / registry["market_run_id"]
        / "discovery_market_input.json"
    )
    market_rows = _index(market_input["rows"])
    registry_bonds = _index(registry["bonds"])
    path_facts = _load_path_facts(data_root, registry)

    batch_run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_research_tasks_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / batch_run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    task_store = data_root / "research_tasks"
    task_store.mkdir(parents=True, exist_ok=True)

    packages = []
    for pending in pending_queue.get("pending_tasks", []):
        code = str(pending["bond_code"]).zfill(6)
        path_id = str(pending["path_id"])
        trigger_key = str(pending["trigger_key"])

        if path_id not in CANONICAL_BY_PATH:
            raise ValueError(f"unsupported path_id: {path_id}")

        bond = registry_bonds[code]
        market_row = market_rows[code]
        path_result = bond["paths"][path_id]
        if path_result.get("economic_status") != "KEEP":
            raise RuntimeError(f"pending task {trigger_key} is no longer Economic KEEP")

        task_id = _task_id(trigger_key)
        package = {
            "task_id": task_id,
            "task_contract_version": TASK_CONTRACT_VERSION,
            "task_status": "READY_FOR_AI_RESEARCH",
            "created_at": _now(),
            "trigger_key": trigger_key,
            "keep_episode_id": pending.get("keep_episode_id"),
            "bond_code": code,
            "bond_name": bond["bond_name"],
            "path_id": path_id,
            "economic_registry_run_id": registry["run_id"],
            "economic_snapshot_id": registry["market_snapshot_id"],
            "market_snapshot_id": registry["market_snapshot_id"],
            "market_cutoff": registry["market_cutoff"],
            "application_commit_sha": deployment.get("application_commit_sha"),
            "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
            "path_research_canonical_path": CANONICAL_BY_PATH[path_id],
            "market_state": {
                key: market_row.get(key)
                for key in (
                    "current_bond_price",
                    "current_stock_price",
                    "current_conversion_price",
                    "current_conversion_value",
                    "remaining_months",
                    "remaining_size",
                    "maturity_date",
                )
            },
            "economic_judgment": path_result,
            "trigger_context": {
                "current_event_state": pending.get("current_event_state"),
                "trigger_reason": pending.get("trigger_reason"),
                "trigger_key": trigger_key,
                "last_triggered_at": pending.get("last_triggered_at"),
            },
            "existing_path_facts": _existing_path_facts(
                path_id,
                code,
                path_facts,
            ),
            "research_questions": RESEARCH_QUESTIONS[path_id],
            "research_rules": {
                "do_not_rerun_discovery": True,
                "do_not_modify_economic_status": True,
                "read_path_research_canonical": True,
                "prefer_primary_evidence": True,
                "preserve_unknown_a": True,
                "block_review_ready_on_material_unknown_b": True,
            },
            "required_result_fields": [
                "path_result_id",
                "task_id",
                "trigger_key",
                "bond_code",
                "bond_name",
                "path_id",
                "research_cutoff",
                "review_ready",
                "research_status",
                "fact_spine",
                "judgments",
                "key_evidence",
                "unknown_a",
                "unknown_b",
                "key_risks",
                "failure_conditions",
                "next_update_nodes",
                "economic_status_at_research",
                "economic_judgment_reference",
            ],
        }
        task_path = task_store / f"{task_id}.json"
        _write_json(task_path, package)
        packages.append({
            "task_id": task_id,
            "trigger_key": trigger_key,
            "bond_code": code,
            "bond_name": bond["bond_name"],
            "path_id": path_id,
            "task_path": str(task_path),
            "task_status": "READY_FOR_AI_RESEARCH",
        })

    result = {
        "run_id": batch_run_id,
        "unit": "PATH_RESEARCH_TASK_BUILDER",
        "task_contract_version": TASK_CONTRACT_VERSION,
        "status": "PASS",
        "created_at": _now(),
        "economic_registry_run_id": registry["run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "market_cutoff": registry["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "pending_queue_count": len(pending_queue.get("pending_tasks", [])),
        "task_packages_built": len(packages),
        "tasks": packages,
    }

    _write_json(run_dir / "path_research_task_batch.json", result)
    _write_json(run_dir / "run_metadata.json", {
        "run_id": batch_run_id,
        "unit": "PATH_RESEARCH_TASK_BUILDER",
        "task_contract_version": TASK_CONTRACT_VERSION,
        "status": "PASS",
        "created_at": result["created_at"],
        "economic_registry_run_id": registry["run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "application_commit_sha": result["application_commit_sha"],
        "knowledge_commit_sha": result["knowledge_commit_sha"],
        "artifacts": {
            "task_batch": str(run_dir / "path_research_task_batch.json"),
            "task_store": str(task_store),
        },
    })

    latest = data_root / "registry" / "latest_path_research_task_batch.json"
    _write_json(latest, {
        "run_id": batch_run_id,
        "economic_registry_run_id": registry["run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "status": "PASS",
        "task_packages_built": len(packages),
        "result_path": str(run_dir / "path_research_task_batch.json"),
        "task_store": str(task_store),
    })
    return result
