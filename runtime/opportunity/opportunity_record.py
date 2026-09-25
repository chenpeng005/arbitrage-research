"""Opportunity Record V1: lossless assembly for Review / View."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OPPORTUNITY_RECORD_VERSION = "opportunity-record-v1"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_path(data_root: Path, value: str | None) -> Path | None:
    if not value:
        return None
    path = Path(value)
    if path.is_absolute():
        return path
    return data_root.parent / path


def _record_state(path_records: list[dict[str, Any]]) -> str:
    states = {str(x.get("research_state") or "") for x in path_records}
    if "HOLD_WAITING_EVIDENCE" in states:
        return "HAS_HOLD"
    if states & {"PENDING", "IN_PROGRESS"}:
        return "RESEARCH_IN_PROGRESS"
    if "COMPLETED" in states:
        return "HAS_COMPLETED_RESEARCH"
    if states and states <= {"NOT_TRIGGERED"}:
        return "ECONOMIC_KEEP_WAITING_TRIGGER"
    raise RuntimeError(f"cannot map bond record_state from {sorted(states)}")


def build_opportunity_records(
    candidate_pool_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    pool = _read_json(candidate_pool_path)

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_opportunity_records_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    records: list[dict[str, Any]] = []
    errors: list[str] = []
    state_counter: Counter[str] = Counter()
    keep_path_count = 0
    seen_pairs: set[tuple[str, str]] = set()

    for bond in pool.get("bonds", []):
        code = str(bond["bond_code"]).zfill(6)
        name = str(bond["bond_name"])
        assembled_paths: list[dict[str, Any]] = []

        for path in bond.get("paths", []):
            path_id = str(path["path_id"])
            pair = (code, path_id)
            if pair in seen_pairs:
                errors.append(f"duplicate candidate path: {code}:{path_id}")
                continue
            seen_pairs.add(pair)

            if path.get("economic_status") != "KEEP":
                errors.append(f"non-KEEP path in Candidate Pool: {code}:{path_id}")
                continue

            research_state = str(path.get("research_state") or "")
            result_payload: dict[str, Any] | None = None
            result_id = path.get("latest_path_result_id")
            result_ref = path.get("latest_path_result_path")

            if research_state in {"COMPLETED", "HOLD_WAITING_EVIDENCE"}:
                resolved = _resolve_path(data_root, result_ref)
                if resolved is None or not resolved.exists():
                    errors.append(
                        f"missing Path Result: {code}:{path_id}:{result_ref!r}"
                    )
                    continue
                result_payload = _read_json(resolved)

                if result_payload.get("path_result_id") != result_id:
                    errors.append(
                        f"path_result_id mismatch: {code}:{path_id}"
                    )
                    continue
                if str(result_payload.get("bond_code") or "").zfill(6) != code:
                    errors.append(
                        f"Path Result bond mismatch: {code}:{path_id}"
                    )
                    continue
                if result_payload.get("path_id") != path_id:
                    errors.append(
                        f"Path Result path mismatch: {code}:{path_id}"
                    )
                    continue
                if result_payload.get("economic_status_at_research") != "KEEP":
                    errors.append(
                        f"Path Result changed Economic KEEP: {code}:{path_id}"
                    )
                    continue

            elif research_state == "NOT_TRIGGERED":
                if result_id is not None or result_ref is not None:
                    errors.append(
                        f"NOT_TRIGGERED has Path Result ref: {code}:{path_id}"
                    )
                    continue
            elif research_state in {"PENDING", "IN_PROGRESS"}:
                # Current research may not have a valid result yet.
                result_payload = None
            else:
                errors.append(
                    f"unsupported research_state={research_state!r}: "
                    f"{code}:{path_id}"
                )
                continue

            assembled_paths.append({
                "path_id": path_id,
                "economic_status": "KEEP",
                "economic_judgment": path["economic_judgment"],
                "current_event_state": path.get("current_event_state"),
                "last_trigger_key": path.get("last_trigger_key"),
                "trigger_reason": path.get("trigger_reason"),
                "research_state": research_state,
                "research_status": path.get("research_status"),
                "review_ready": path.get("review_ready"),
                "latest_path_result_id": result_id,
                "latest_path_result_path": result_ref,
                "research_history_count": path.get("research_history_count", 0),
                "path_result": result_payload,
            })
            keep_path_count += 1

        try:
            record_state = _record_state(assembled_paths)
        except Exception as exc:
            errors.append(f"{code}: {type(exc).__name__}: {exc}")
            record_state = "INVALID"

        state_counter[record_state] += 1
        records.append({
            "bond_code": code,
            "bond_name": name,
            "market_snapshot_id": bond.get("market_snapshot_id"),
            "market_cutoff": bond.get("market_cutoff"),
            "keep_path_count": len(assembled_paths),
            "record_state": record_state,
            "paths": assembled_paths,
        })

    expected_bonds = int(pool.get("bond_count") or 0)
    expected_paths = int(pool.get("keep_path_count") or 0)

    if len(records) != expected_bonds:
        errors.append(
            f"bond_count mismatch: built={len(records)}, expected={expected_bonds}"
        )
    if keep_path_count != expected_paths:
        errors.append(
            f"keep_path_count mismatch: built={keep_path_count}, "
            f"expected={expected_paths}"
        )

    status = "PASS" if not errors else "FAIL"
    result = {
        "opportunity_record_version": OPPORTUNITY_RECORD_VERSION,
        "run_id": run_id,
        "unit": "OPPORTUNITY_RECORD",
        "status": status,
        "created_at": _now(),
        "candidate_pool_run_id": pool["run_id"],
        "economic_registry_run_id": pool["economic_registry_run_id"],
        "market_snapshot_id": pool["market_snapshot_id"],
        "market_cutoff": pool["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "bond_count": len(records),
        "keep_path_count": keep_path_count,
        "record_state_summary": dict(sorted(state_counter.items())),
        "errors": errors,
        "records": records,
    }

    result_path = run_dir / "opportunity_records.json"
    _write_json(result_path, result)
    _write_json(run_dir / "opportunity_records_audit.json", {
        "status": status,
        "candidate_pool_run_id": pool["run_id"],
        "expected_bond_count": expected_bonds,
        "record_bond_count": len(records),
        "expected_keep_path_count": expected_paths,
        "record_keep_path_count": keep_path_count,
        "record_state_summary": dict(sorted(state_counter.items())),
        "errors": errors,
    })

    if status == "PASS":
        _write_json(
            data_root / "registry" / "latest_opportunity_records.json",
            {
                "run_id": run_id,
                "status": status,
                "opportunity_record_version": OPPORTUNITY_RECORD_VERSION,
                "candidate_pool_run_id": pool["run_id"],
                "market_snapshot_id": pool["market_snapshot_id"],
                "market_cutoff": pool["market_cutoff"],
                "bond_count": len(records),
                "keep_path_count": keep_path_count,
                "result_path": str(result_path),
            },
        )

    return result
