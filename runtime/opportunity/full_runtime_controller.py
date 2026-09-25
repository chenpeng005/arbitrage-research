"""Full Opportunity Discovery Runtime Controller V1.

Orchestrates existing runtime units after one validated FORMAL_CLOSE Market Map.
It owns sequencing, status persistence and stop conditions only; it never
recomputes or overrides Path-local economic or research judgments.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from runtime.opportunity.candidate_pool import build_candidate_pool
from runtime.opportunity.discovery_controller import run_opportunity_discovery
from runtime.opportunity.ingress import build_market_ingress
from runtime.opportunity.opportunity_record import build_opportunity_records
from runtime.opportunity.path_research_batch import run_path_research_batch
from runtime.opportunity.research_evidence import build_research_evidence
from runtime.opportunity.research_task_builder import build_path_research_tasks
from runtime.opportunity.research_trigger import run_research_trigger

FULL_RUNTIME_VERSION = "opportunity-full-runtime-v1"
StageCallback = Callable[[str, str, dict[str, Any]], None]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_path(data_root: Path, run_id: str, filename: str) -> Path:
    return data_root / "runs" / run_id / filename


def _pending_count(data_root: Path) -> int:
    path = data_root / "registry" / "pending_research_tasks.json"
    if not path.exists():
        return 0
    payload = _read_json(path)
    return len(payload.get("pending_tasks", []))


def run_opportunity_full_downstream(
    *,
    formal_entry_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
    provider_name: str,
    model: str,
    research_batch_limit: int = 5,
    max_research_rounds: int = 20,
    wall_timeout_seconds: int = 180,
    run_research: bool = True,
    stage_callback: StageCallback | None = None,
) -> dict[str, Any]:
    entry = _read_json(formal_entry_path)
    if entry.get("snapshot_class") != "FORMAL_CLOSE":
        raise RuntimeError("full runtime requires one FORMAL_CLOSE Market Map")
    if entry.get("output_contract_status") != "PASS":
        raise RuntimeError("formal Market Map output contract is not PASS")
    snapshot_id = str(entry.get("snapshot_id") or "")
    if not snapshot_id:
        raise RuntimeError("formal Market Map snapshot_id is missing")
    manifest_path = Path(str(entry["output_contract_path"]))
    if not manifest_path.exists():
        raise RuntimeError("formal Market Map output contract is missing")

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_full_runtime_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "full_runtime_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    status_path = run_dir / "status.json"

    state: dict[str, Any] = {
        "full_runtime_version": FULL_RUNTIME_VERSION,
        "run_id": run_id,
        "status": "RUNNING",
        "started_at": _now(),
        "updated_at": _now(),
        "market_snapshot_id": snapshot_id,
        "market_cutoff": entry.get("market_cutoff"),
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "current_stage": None,
        "stages": [],
        "artifacts": {},
    }

    def mark(stage: str, status: str, **meta: Any) -> None:
        event = {"stage": stage, "status": status, "at": _now(), **meta}
        state["current_stage"] = stage
        state["updated_at"] = event["at"]
        state["stages"].append(event)
        _write_json(status_path, state)
        if stage_callback:
            stage_callback(stage, status, meta)
    try:
        mark("MARKET_INGRESS", "RUNNING")
        ingress = build_market_ingress(
            manifest_path,
            data_root,
            deployment,
            expected_snapshot_id=snapshot_id,
        )
        ingress_path = _result_path(
            data_root, ingress["run_id"], "discovery_market_input.json"
        )
        _write_json(
            data_root / "registry" / "latest_discovery_market_ingress.json",
            {
                "run_id": ingress["run_id"],
                "market_snapshot_id": ingress["market_snapshot_id"],
            },
        )
        state["artifacts"]["market_ingress"] = str(ingress_path)
        mark("MARKET_INGRESS", "PASS", rows=ingress["audit"]["market_rows"])

        mark("ECONOMIC_DISCOVERY", "RUNNING")
        discovery = run_opportunity_discovery(ingress_path, data_root, deployment)
        if discovery.get("status") != "PASS":
            raise RuntimeError(
                f"economic discovery ended with status={discovery.get('status')}"
            )
        registry_path = _result_path(
            data_root, discovery["run_id"], "economic_path_registry.json"
        )
        state["artifacts"]["economic_path_registry"] = str(registry_path)
        mark(
            "ECONOMIC_DISCOVERY",
            "PASS",
            opportunity_bonds=discovery["bonds_with_any_keep"],
        )

        mark("RESEARCH_TRIGGER", "RUNNING")
        trigger = run_research_trigger(registry_path, data_root, deployment)
        pending = int(trigger.get("pending_tasks_total") or 0)
        state["artifacts"]["research_trigger"] = str(
            _result_path(data_root, trigger["run_id"], "research_trigger_result.json")
        )
        mark("RESEARCH_TRIGGER", "PASS", pending_tasks=pending)
        task_batch = None
        evidence_batch = None
        research_rounds: list[dict[str, Any]] = []
        if pending:
            mark("RESEARCH_TASKS", "RUNNING", pending_tasks=pending)
            task_batch = build_path_research_tasks(
                registry_path,
                data_root / "registry" / "pending_research_tasks.json",
                data_root,
                deployment,
            )
            task_batch_path = _result_path(
                data_root, task_batch["run_id"], "path_research_task_batch.json"
            )
            state["artifacts"]["research_tasks"] = str(task_batch_path)
            mark(
                "RESEARCH_TASKS",
                "PASS",
                task_packages=task_batch.get("task_packages_built", 0),
            )

            mark("RESEARCH_EVIDENCE", "RUNNING")
            evidence_batch = build_research_evidence(
                task_batch_path, data_root, deployment
            )
            state["artifacts"]["research_evidence"] = str(
                _result_path(
                    data_root,
                    evidence_batch["run_id"],
                    "path_research_evidence_batch.json",
                )
            )
            mark(
                "RESEARCH_EVIDENCE",
                "PASS",
                evidence_packs=evidence_batch.get("packs_built", 0),
            )
            if run_research:
                mark("PATH_RESEARCH", "RUNNING", pending_tasks=_pending_count(data_root))
                previous_pending = _pending_count(data_root)
                for round_no in range(1, max_research_rounds + 1):
                    if previous_pending <= 0:
                        break
                    batch = run_path_research_batch(
                        root=data_root.parent,
                        data_root=data_root,
                        provider_name=provider_name,
                        model=model,
                        limit=research_batch_limit,
                        retry_once=False,
                        wall_timeout_seconds=wall_timeout_seconds,
                    )
                    research_rounds.append(batch)
                    current_pending = int(batch.get("remaining_pending") or 0)
                    selected = int(batch.get("selected") or 0)
                    if current_pending <= 0:
                        previous_pending = 0
                        break
                    if current_pending >= previous_pending and selected <= 0:
                        previous_pending = current_pending
                        break
                    previous_pending = current_pending
                mark(
                    "PATH_RESEARCH",
                    "PASS" if previous_pending == 0 else "PARTIAL",
                    rounds=len(research_rounds),
                    remaining_pending=previous_pending,
                )
            else:
                mark("PATH_RESEARCH", "SKIPPED", pending_tasks=pending)
        else:
            mark("RESEARCH_TASKS", "SKIPPED", pending_tasks=0)
            mark("RESEARCH_EVIDENCE", "SKIPPED", pending_tasks=0)
            mark("PATH_RESEARCH", "SKIPPED", pending_tasks=0)

        mark("CANDIDATE_POOL", "RUNNING")
        pool = build_candidate_pool(registry_path, data_root, deployment)
        if pool.get("status") != "PASS":
            raise RuntimeError("Candidate Pool audit failed")
        pool_path = _result_path(data_root, pool["run_id"], "candidate_pool.json")
        state["artifacts"]["candidate_pool"] = str(pool_path)
        mark(
            "CANDIDATE_POOL",
            "PASS",
            bond_count=pool["bond_count"],
            keep_path_count=pool["keep_path_count"],
            research_state_summary=pool.get("research_state_summary", {}),
        )

        mark("OPPORTUNITY_RECORD", "RUNNING")
        records = build_opportunity_records(pool_path, data_root, deployment)
        if records.get("status") != "PASS":
            raise RuntimeError("Opportunity Record audit failed")
        record_path = _result_path(
            data_root, records["run_id"], "opportunity_records.json"
        )
        state["artifacts"]["opportunity_records"] = str(record_path)
        mark(
            "OPPORTUNITY_RECORD",
            "PASS",
            bond_count=records["bond_count"],
            keep_path_count=records["keep_path_count"],
            record_state_summary=records.get("record_state_summary", {}),
        )

        state["status"] = "PASS"
        state["current_stage"] = "COMPLETE"
        state["completed_at"] = _now()
        state["updated_at"] = state["completed_at"]
        state["summary"] = {
            "opportunity_bonds": records["bond_count"],
            "keep_paths": records["keep_path_count"],
            "research_state_summary": pool.get("research_state_summary", {}),
            "remaining_pending_research": _pending_count(data_root),
        }
        _write_json(status_path, state)
        _write_json(
            data_root / "registry" / "latest_full_runtime.json",
            {
                "run_id": run_id,
                "status": state["status"],
                "market_snapshot_id": snapshot_id,
                "market_cutoff": entry.get("market_cutoff"),
                "status_path": str(status_path),
                "opportunity_records_path": str(record_path),
            },
        )
        if stage_callback:
            stage_callback("COMPLETE", "PASS", state["summary"])
        return state
    except Exception as exc:
        state["status"] = "FAIL"
        state["completed_at"] = _now()
        state["updated_at"] = state["completed_at"]
        state["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(status_path, state)
        if stage_callback:
            stage_callback(
                state.get("current_stage") or "UNKNOWN",
                "FAIL",
                {"error": state["error"]},
            )
        raise
