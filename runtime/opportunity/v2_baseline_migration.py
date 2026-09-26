"""One-time Path Research V1 -> V2 baseline migration.

This migration is intentionally two-phase:
1) build V2 Task/Evidence and run AI + Validator in isolation;
2) promote only when every target is COMPLETED + review_ready=true.

Normal Research Trigger semantics are never replaced by migration trigger keys.
The current trigger_key is preserved while migration task_ids are namespaced so
legacy Path Result files remain available for audit.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.ai_runtime.engine import run_ai_job
from runtime.opportunity.candidate_pool import build_candidate_pool
from runtime.opportunity.opportunity_record import build_opportunity_records
from runtime.opportunity.research_ai_input import prepare_path_research_ai_input
from runtime.opportunity.research_evidence import build_research_evidence
from runtime.opportunity.research_ledger import record_path_research_result
from runtime.opportunity.research_task_builder import build_path_research_tasks

MIGRATION_VERSION = "path-research-v2-baseline-migration-v1"
SMOKE_PAIRS = [
    ("110075", "MATURITY_CASH"),
    ("110092", "PUT"),
    ("127089", "DOWNWARD_REVISION"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_v2(result: dict[str, Any]) -> bool:
    return (
        isinstance(result.get("summary"), dict)
        and isinstance(result.get("logic_chain"), list)
        and bool(result.get("logic_chain"))
        and isinstance(result.get("engineering_anchor_reference"), dict)
    )


def _migration_task_id(trigger_key: str, knowledge_sha: str) -> str:
    digest = hashlib.sha256(
        f"V2_BASELINE|{trigger_key}|{knowledge_sha}".encode("utf-8")
    ).hexdigest()[:16]
    return f"research_v2m_{digest}"


def _latest_registry(data_root: Path) -> tuple[dict[str, Any], Path]:
    pointer = _read_json(data_root / "registry" / "latest_economic_path_registry.json")
    path = Path(str(pointer["result_path"]))
    if not path.exists():
        raise FileNotFoundError(path)
    return pointer, path


def _collect_targets(data_root: Path) -> list[dict[str, Any]]:
    state = _read_json(data_root / "registry" / "research_trigger_state.json")
    ledger = _read_json(data_root / "registry" / "research_ledger.json")
    state_paths = state.get("paths", {})
    ledger_rows = ledger.get("results", {})
    targets: list[dict[str, Any]] = []

    for state_key, row in state_paths.items():
        if row.get("research_status") != "COMPLETED":
            continue
        trigger_key = row.get("last_trigger_key")
        if not trigger_key:
            continue
        entry = ledger_rows.get(trigger_key)
        if not entry:
            raise RuntimeError(f"missing ledger entry for current COMPLETED path: {state_key}")
        result_path = Path(str(entry.get("result_path") or ""))
        if not result_path.exists():
            raise FileNotFoundError(result_path)
        result = _read_json(result_path)
        if _is_v2(result):
            continue
        code = str(entry["bond_code"]).zfill(6)
        path_id = str(entry["path_id"])
        targets.append(
            {
                "state_key": state_key,
                "bond_code": code,
                "bond_name": entry.get("bond_name"),
                "path_id": path_id,
                "trigger_key": trigger_key,
                "keep_episode_id": row.get("keep_episode_id"),
                "current_event_state": row.get("current_event_state"),
                "trigger_reason": row.get("last_trigger_reason"),
                "last_triggered_at": row.get("last_triggered_at"),
                "legacy_task_id": entry.get("task_id"),
                "legacy_path_result_id": entry.get("path_result_id"),
                "legacy_result_path": str(result_path),
            }
        )

    targets.sort(key=lambda x: (x["path_id"], x["bond_code"]))
    return targets


def _backup_file(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _restore_file(src: Path, dst: Path) -> None:
    if src.exists():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def prepare(root: Path, data_root: Path) -> dict[str, Any]:
    deployment = _read_json(data_root / "deployment_manifest.json")
    registry_pointer, registry_path = _latest_registry(data_root)
    targets = _collect_targets(data_root)
    if not targets:
        raise RuntimeError("no current legacy COMPLETED Path Results require V2 migration")

    migration_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_path_research_v2_baseline"
    )
    migration_dir = data_root / "migrations" / "path_research_v2" / migration_id
    migration_dir.mkdir(parents=True, exist_ok=False)
    backup_dir = migration_dir / "backup"

    registry_files = [
        "research_ledger.json",
        "research_trigger_state.json",
        "pending_research_tasks.json",
        "latest_candidate_pool.json",
        "latest_opportunity_records.json",
        "latest_path_research_task_batch.json",
        "latest_path_research_evidence.json",
    ]
    for name in registry_files:
        _backup_file(data_root / "registry" / name, backup_dir / "registry" / name)

    # Preserve every legacy result/task/evidence before any deterministic task path
    # can be overwritten by the V2 Task Builder.
    for target in targets:
        result_path = Path(target["legacy_result_path"])
        _backup_file(
            result_path,
            backup_dir / "legacy_path_results" / result_path.name,
        )
        old_task_id = str(target.get("legacy_task_id") or "")
        if old_task_id:
            _backup_file(
                data_root / "research_tasks" / f"{old_task_id}.json",
                backup_dir / "legacy_research_tasks" / f"{old_task_id}.json",
            )
            _backup_file(
                data_root / "research_evidence" / f"{old_task_id}.json",
                backup_dir / "legacy_research_evidence" / f"{old_task_id}.json",
            )

    queue = {
        "migration_version": MIGRATION_VERSION,
        "economic_registry_run_id": registry_pointer["run_id"],
        "market_snapshot_id": registry_pointer["market_snapshot_id"],
        "market_cutoff": registry_pointer["market_cutoff"],
        "created_at": _now(),
        "pending_tasks": [
            {
                "bond_code": t["bond_code"],
                "bond_name": t["bond_name"],
                "path_id": t["path_id"],
                "economic_status": "KEEP",
                "keep_episode_id": t["keep_episode_id"],
                "current_event_state": t["current_event_state"],
                "trigger_key": t["trigger_key"],
                "trigger_reason": t["trigger_reason"],
                "last_triggered_at": t["last_triggered_at"],
                "research_status": "MIGRATION_V2_BASELINE",
            }
            for t in targets
        ],
    }
    queue_path = migration_dir / "migration_queue.json"
    _write_json(queue_path, queue)

    task_batch = build_path_research_tasks(
        registry_path,
        queue_path,
        data_root,
        deployment,
    )
    generated_batch_path = (
        data_root / "runs" / task_batch["run_id"] / "path_research_task_batch.json"
    )
    generated_batch = _read_json(generated_batch_path)

    target_by_trigger = {t["trigger_key"]: t for t in targets}
    transformed_tasks = []
    for item in generated_batch["tasks"]:
        trigger_key = str(item["trigger_key"])
        target = target_by_trigger[trigger_key]
        source_task_path = Path(str(item["task_path"]))
        package = _read_json(source_task_path)
        migration_task_id = _migration_task_id(
            trigger_key,
            str(deployment.get("knowledge_commit_sha") or ""),
        )
        package["task_id"] = migration_task_id
        package["migration_context"] = {
            "migration_version": MIGRATION_VERSION,
            "migration_id": migration_id,
            "legacy_task_id": target.get("legacy_task_id"),
            "legacy_path_result_id": target.get("legacy_path_result_id"),
            "legacy_result_path": target.get("legacy_result_path"),
        }
        migration_task_path = data_root / "research_tasks" / f"{migration_task_id}.json"
        _write_json(migration_task_path, package)

        target["migration_task_id"] = migration_task_id
        target["migration_task_path"] = str(migration_task_path)
        transformed_tasks.append(
            {
                **item,
                "task_id": migration_task_id,
                "task_path": str(migration_task_path),
            }
        )

    transformed_batch = {
        **generated_batch,
        "run_id": migration_id,
        "unit": "PATH_RESEARCH_V2_BASELINE_MIGRATION_TASKS",
        "migration_version": MIGRATION_VERSION,
        "tasks": transformed_tasks,
        "task_packages_built": len(transformed_tasks),
    }
    transformed_batch_path = migration_dir / "v2_task_batch.json"
    _write_json(transformed_batch_path, transformed_batch)

    evidence_batch = build_research_evidence(
        transformed_batch_path,
        data_root,
        deployment,
    )

    # Restore normal-runtime latest task/evidence pointers and legacy deterministic
    # task files. Migration-specific namespaced tasks/evidence stay available.
    for name in (
        "latest_path_research_task_batch.json",
        "latest_path_research_evidence.json",
    ):
        _restore_file(
            backup_dir / "registry" / name,
            data_root / "registry" / name,
        )
    for target in targets:
        old_task_id = str(target.get("legacy_task_id") or "")
        if old_task_id:
            _restore_file(
                backup_dir / "legacy_research_tasks" / f"{old_task_id}.json",
                data_root / "research_tasks" / f"{old_task_id}.json",
            )
            _restore_file(
                backup_dir / "legacy_research_evidence" / f"{old_task_id}.json",
                data_root / "research_evidence" / f"{old_task_id}.json",
            )

    smoke_keys = {
        f"{code}:{path_id}" for code, path_id in SMOKE_PAIRS
    }
    for target in targets:
        target["smoke"] = (
            f"{target['bond_code']}:{target['path_id']}" in smoke_keys
        )

    plan = {
        "migration_version": MIGRATION_VERSION,
        "migration_id": migration_id,
        "status": "PREPARED",
        "created_at": _now(),
        "root": str(root),
        "data_root": str(data_root),
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "economic_registry_run_id": registry_pointer["run_id"],
        "market_snapshot_id": registry_pointer["market_snapshot_id"],
        "market_cutoff": registry_pointer["market_cutoff"],
        "target_count": len(targets),
        "smoke_count": sum(bool(t["smoke"]) for t in targets),
        "targets": targets,
        "task_batch_path": str(transformed_batch_path),
        "evidence_batch_run_id": evidence_batch["run_id"],
        "backup_dir": str(backup_dir),
    }
    _write_json(migration_dir / "plan.json", plan)
    _write_json(
        data_root / "registry" / "latest_path_research_v2_migration.json",
        {
            "migration_id": migration_id,
            "status": "PREPARED",
            "plan_path": str(migration_dir / "plan.json"),
        },
    )
    return plan


def _load_plan(data_root: Path, migration_id: str | None) -> tuple[Path, dict[str, Any]]:
    if migration_id:
        migration_dir = data_root / "migrations" / "path_research_v2" / migration_id
        plan_path = migration_dir / "plan.json"
    else:
        pointer = _read_json(
            data_root / "registry" / "latest_path_research_v2_migration.json"
        )
        plan_path = Path(str(pointer["plan_path"]))
        migration_dir = plan_path.parent
    return migration_dir, _read_json(plan_path)


def run_validations(
    root: Path,
    data_root: Path,
    migration_id: str | None,
    mode: str,
) -> dict[str, Any]:
    migration_dir, plan = _load_plan(data_root, migration_id)
    provider_name = os.environ.get("AI_PROVIDER", "deepseek")
    model = os.environ.get("AI_MODEL", "deepseek-chat")
    selected = [
        t for t in plan["targets"]
        if mode == "all" or bool(t.get("smoke"))
    ]
    results_dir = migration_dir / "validated_results"
    results_dir.mkdir(exist_ok=True)
    run_rows = []

    for target in selected:
        task_id = str(target["migration_task_id"])
        accepted_path = results_dir / f"{task_id}.json"
        meta_path = results_dir / f"{task_id}.meta.json"
        if accepted_path.exists() and meta_path.exists():
            meta = _read_json(meta_path)
            if meta.get("accepted") is True:
                run_rows.append(meta)
                continue

        descriptor = prepare_path_research_ai_input(task_id, data_root)
        work_dir = Path(descriptor["work_dir"])
        ai_result = run_ai_job(
            root=root,
            data_root=data_root,
            task_type="PATH_RESEARCH",
            input_file=Path(descriptor["input_path"]),
            business_run_dir=work_dir,
            provider_name=provider_name,
            provider_config={},
            model_config={
                "model": model,
                "temperature": 0.1,
                "max_tokens": 12000,
            },
        )

        row = {
            "task_id": task_id,
            "bond_code": target["bond_code"],
            "bond_name": target["bond_name"],
            "path_id": target["path_id"],
            "ai_status": ai_result.get("status"),
            "ai_job_id": ai_result.get("ai_job_id"),
            "accepted": False,
            "reason": None,
            "validated_at": _now(),
        }
        if ai_result.get("status") == "PASS":
            result_path = (
                data_root
                / "ai_jobs"
                / str(ai_result["ai_job_id"])
                / "structured_output.json"
            )
            result = _read_json(result_path)
            if not _is_v2(result):
                row["reason"] = "validator passed but result does not have V2 shape"
            elif result.get("research_status") != "COMPLETED":
                row["reason"] = (
                    f"research_status={result.get('research_status')!r}, "
                    "baseline migration requires COMPLETED"
                )
            elif result.get("review_ready") is not True:
                row["reason"] = "review_ready is not true"
            else:
                _write_json(accepted_path, result)
                row["accepted"] = True
                row["reason"] = "PASS"
        else:
            row["reason"] = ai_result.get("error") or ai_result.get("status")

        _write_json(meta_path, row)
        run_rows.append(row)

    all_meta = []
    for target in plan["targets"]:
        task_id = str(target["migration_task_id"])
        meta_path = results_dir / f"{task_id}.meta.json"
        if meta_path.exists():
            all_meta.append(_read_json(meta_path))

    accepted_count = sum(x.get("accepted") is True for x in all_meta)
    smoke_meta = [
        x for x in all_meta
        if any(
            x["bond_code"] == code and x["path_id"] == path_id
            for code, path_id in SMOKE_PAIRS
        )
    ]
    smoke_pass = (
        len(smoke_meta) == len(SMOKE_PAIRS)
        and all(x.get("accepted") is True for x in smoke_meta)
    )
    all_pass = (
        accepted_count == int(plan["target_count"])
        and len(all_meta) == int(plan["target_count"])
    )
    status_payload = {
        "migration_id": plan["migration_id"],
        "mode": mode,
        "run_at": _now(),
        "target_count": plan["target_count"],
        "selected_count": len(selected),
        "validated_count": len(all_meta),
        "accepted_count": accepted_count,
        "smoke_pass": smoke_pass,
        "all_pass": all_pass,
        "rows": run_rows,
    }
    _write_json(migration_dir / "validation_status.json", status_payload)
    pointer_path = data_root / "registry" / "latest_path_research_v2_migration.json"
    pointer = _read_json(pointer_path)
    pointer["status"] = "AI_VALIDATED" if all_pass else (
        "SMOKE_PASS" if smoke_pass else "VALIDATION_INCOMPLETE"
    )
    pointer["validated_count"] = len(all_meta)
    pointer["accepted_count"] = accepted_count
    _write_json(pointer_path, pointer)
    return status_payload


def promote(
    root: Path,
    data_root: Path,
    migration_id: str | None,
) -> dict[str, Any]:
    migration_dir, plan = _load_plan(data_root, migration_id)
    results_dir = migration_dir / "validated_results"
    deployment = _read_json(data_root / "deployment_manifest.json")

    latest_registry_pointer, registry_path = _latest_registry(data_root)
    if latest_registry_pointer["run_id"] != plan["economic_registry_run_id"]:
        raise RuntimeError("economic registry changed after migration prepare; abort promotion")
    if latest_registry_pointer["market_snapshot_id"] != plan["market_snapshot_id"]:
        raise RuntimeError("market snapshot changed after migration prepare; abort promotion")
    if deployment.get("knowledge_commit_sha") != plan["knowledge_commit_sha"]:
        raise RuntimeError("knowledge deployment changed after migration prepare")
    if deployment.get("application_commit_sha") != plan["application_commit_sha"]:
        raise RuntimeError("application deployment changed after migration prepare")

    state = _read_json(data_root / "registry" / "research_trigger_state.json")
    ledger = _read_json(data_root / "registry" / "research_ledger.json")
    for target in plan["targets"]:
        task_id = str(target["migration_task_id"])
        meta_path = results_dir / f"{task_id}.meta.json"
        result_path = results_dir / f"{task_id}.json"
        if not meta_path.exists() or not result_path.exists():
            raise RuntimeError(f"migration result missing: {task_id}")
        meta = _read_json(meta_path)
        result = _read_json(result_path)
        if meta.get("accepted") is not True:
            raise RuntimeError(f"migration result not accepted: {task_id}")
        if not _is_v2(result) or result.get("research_status") != "COMPLETED":
            raise RuntimeError(f"migration result is not promotable V2: {task_id}")
        row = state.get("paths", {}).get(target["state_key"])
        if not row:
            raise RuntimeError(f"current trigger state missing: {target['state_key']}")
        if row.get("last_trigger_key") != target["trigger_key"]:
            raise RuntimeError(f"trigger key changed: {target['state_key']}")
        if row.get("research_status") != "COMPLETED":
            raise RuntimeError(f"research status changed: {target['state_key']}")
        current_entry = ledger.get("results", {}).get(target["trigger_key"])
        if not current_entry:
            raise RuntimeError(f"ledger entry disappeared: {target['trigger_key']}")
        if current_entry.get("path_result_id") != target["legacy_path_result_id"]:
            raise RuntimeError(f"ledger result changed since prepare: {target['state_key']}")

    backup_dir = Path(str(plan["backup_dir"]))
    promoted = []
    try:
        for target in plan["targets"]:
            task_id = str(target["migration_task_id"])
            result_path = results_dir / f"{task_id}.json"
            writeback = record_path_research_result(result_path, data_root)
            promoted.append(
                {
                    "bond_code": target["bond_code"],
                    "path_id": target["path_id"],
                    "trigger_key": target["trigger_key"],
                    "new_path_result_id": writeback["path_result_id"],
                }
            )

        pool = build_candidate_pool(registry_path, data_root, deployment)
        if pool.get("status") != "PASS":
            raise RuntimeError("Candidate Pool rebuild failed after V2 promotion")
        pool_path = (
            data_root / "runs" / pool["run_id"] / "candidate_pool.json"
        )
        records = build_opportunity_records(pool_path, data_root, deployment)
        if records.get("status") != "PASS":
            raise RuntimeError("Opportunity Record rebuild failed after V2 promotion")

        # Verify every current COMPLETED formal result now has V2 shape.
        current_state = _read_json(
            data_root / "registry" / "research_trigger_state.json"
        )
        current_ledger = _read_json(data_root / "registry" / "research_ledger.json")
        v2_count = 0
        completed_count = 0
        legacy = []
        for state_key, row in current_state.get("paths", {}).items():
            if row.get("research_status") != "COMPLETED":
                continue
            completed_count += 1
            entry = current_ledger.get("results", {}).get(row.get("last_trigger_key"))
            if not entry:
                legacy.append(state_key)
                continue
            result = _read_json(Path(str(entry["result_path"])))
            if _is_v2(result):
                v2_count += 1
            else:
                legacy.append(state_key)
        if legacy:
            raise RuntimeError(
                "promotion audit found non-V2 COMPLETED results: " + ", ".join(legacy)
            )

        payload = {
            "migration_version": MIGRATION_VERSION,
            "migration_id": plan["migration_id"],
            "status": "PASS",
            "promoted_at": _now(),
            "promoted_count": len(promoted),
            "completed_formal_count": completed_count,
            "v2_formal_count": v2_count,
            "candidate_pool_run_id": pool["run_id"],
            "opportunity_records_run_id": records["run_id"],
            "promoted": promoted,
        }
        _write_json(migration_dir / "promotion_result.json", payload)
        pointer_path = data_root / "registry" / "latest_path_research_v2_migration.json"
        pointer = _read_json(pointer_path)
        pointer["status"] = "PASS"
        pointer["promoted_count"] = len(promoted)
        pointer["promotion_result_path"] = str(
            migration_dir / "promotion_result.json"
        )
        _write_json(pointer_path, pointer)
        return payload
    except Exception:
        # Fail closed: restore all formal registry state. Newly-created V2 Path Result
        # files are harmless orphan artifacts and remain auditable.
        for name in (
            "research_ledger.json",
            "research_trigger_state.json",
            "pending_research_tasks.json",
            "latest_candidate_pool.json",
            "latest_opportunity_records.json",
        ):
            _restore_file(
                backup_dir / "registry" / name,
                data_root / "registry" / name,
            )
        raise


def status(data_root: Path, migration_id: str | None) -> dict[str, Any]:
    migration_dir, plan = _load_plan(data_root, migration_id)
    result = {
        "migration_id": plan["migration_id"],
        "target_count": plan["target_count"],
        "market_cutoff": plan["market_cutoff"],
        "application_commit_sha": plan["application_commit_sha"],
        "knowledge_commit_sha": plan["knowledge_commit_sha"],
        "plan_status": plan.get("status"),
    }
    validation = migration_dir / "validation_status.json"
    promotion = migration_dir / "promotion_result.json"
    if validation.exists():
        result["validation"] = _read_json(validation)
    if promotion.exists():
        result["promotion"] = _read_json(promotion)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["prepare", "smoke", "run-all", "promote", "status"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--data-root", default="runtime_data")
    parser.add_argument("--migration-id")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    data_root = Path(args.data_root).resolve()

    if args.command == "prepare":
        payload = prepare(root, data_root)
    elif args.command == "smoke":
        payload = run_validations(root, data_root, args.migration_id, "smoke")
    elif args.command == "run-all":
        payload = run_validations(root, data_root, args.migration_id, "all")
    elif args.command == "promote":
        payload = promote(root, data_root, args.migration_id)
    else:
        payload = status(data_root, args.migration_id)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
