"""Candidate Pool V1: lossless aggregation of all Economic KEEP paths."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CANDIDATE_POOL_VERSION = "candidate-pool-v1"
HOLD_STATUSES = {"NEEDS_EVIDENCE", "UNRESOLVED"}


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


def _research_state(
    trigger_state: dict[str, Any],
) -> str:
    last_trigger_key = trigger_state.get("last_trigger_key")
    status = trigger_state.get("research_status")

    if not last_trigger_key:
        if status not in (None, ""):
            raise RuntimeError(
                "research_status exists while last_trigger_key is null"
            )
        return "NOT_TRIGGERED"

    if status == "COMPLETED":
        return "COMPLETED"
    if status in HOLD_STATUSES:
        return "HOLD_WAITING_EVIDENCE"
    if status == "PENDING":
        return "PENDING"
    if status == "IN_PROGRESS":
        return "IN_PROGRESS"

    raise RuntimeError(
        f"cannot map research state: trigger_key={last_trigger_key!r}, "
        f"research_status={status!r}"
    )


def build_candidate_pool(
    economic_registry_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    registry = _read_json(economic_registry_path)
    trigger_state = _read_json(
        data_root / "registry" / "research_trigger_state.json"
    )
    ledger = _read_json(
        data_root / "registry" / "research_ledger.json"
    )

    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_candidate_pool_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)

    trigger_paths = trigger_state.get("paths", {})
    ledger_results = ledger.get("results", {})

    history_by_pair: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in ledger_results.values():
        pair = (
            str(entry.get("bond_code") or "").zfill(6),
            str(entry.get("path_id") or ""),
        )
        history_by_pair.setdefault(pair, []).append(entry)

    bonds: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    research_counter: Counter[str] = Counter()
    keep_path_count = 0
    expected_keep_path_count = 0
    expected_bond_count = 0
    errors: list[str] = []

    for bond in registry.get("bonds", []):
        code = str(bond["bond_code"]).zfill(6)
        name = str(bond["bond_name"])
        keep_paths = [
            (path_id, path)
            for path_id, path in (bond.get("paths") or {}).items()
            if path.get("economic_status") == "KEEP"
        ]

        if not keep_paths:
            continue

        expected_bond_count += 1
        expected_keep_path_count += len(keep_paths)
        path_records: list[dict[str, Any]] = []

        for path_id, economic in keep_paths:
            pair = (code, path_id)
            if pair in seen_pairs:
                errors.append(f"duplicate KEEP pair: {code}:{path_id}")
                continue
            seen_pairs.add(pair)

            state_key = f"{code}:{path_id}"
            state = trigger_paths.get(state_key)
            if state is None:
                errors.append(f"missing trigger state: {state_key}")
                continue
            if state.get("economic_status") != "KEEP":
                errors.append(
                    f"trigger economic status mismatch: {state_key} "
                    f"{state.get('economic_status')!r}"
                )
                continue

            try:
                research_state = _research_state(state)
            except Exception as exc:
                errors.append(f"{state_key}: {type(exc).__name__}: {exc}")
                continue

            last_trigger_key = state.get("last_trigger_key")
            latest_ledger: dict[str, Any] | None = None
            latest_result_path: str | None = None
            latest_result_id: str | None = None
            review_ready: bool | None = None

            if last_trigger_key:
                latest_ledger = ledger_results.get(last_trigger_key)
                if research_state in {"COMPLETED", "HOLD_WAITING_EVIDENCE"}:
                    if latest_ledger is None:
                        errors.append(
                            f"missing ledger entry for current trigger: "
                            f"{state_key}:{last_trigger_key}"
                        )
                        continue

                    latest_result_id = latest_ledger.get("path_result_id")
                    latest_result_path = latest_ledger.get("result_path")
                    review_ready = latest_ledger.get("review_ready")

                    state_result_id = state.get("last_path_result_id")
                    if state_result_id != latest_result_id:
                        errors.append(
                            f"path result id mismatch: {state_key}: "
                            f"state={state_result_id!r}, "
                            f"ledger={latest_result_id!r}"
                        )
                        continue

                    resolved = _resolve_path(data_root, latest_result_path)
                    if resolved is None or not resolved.exists():
                        errors.append(
                            f"path result file missing: {state_key}: "
                            f"{latest_result_path!r}"
                        )
                        continue

                    if research_state == "COMPLETED" and review_ready is not True:
                        errors.append(
                            f"COMPLETED path is not review_ready: {state_key}"
                        )
                        continue
                    if (
                        research_state == "HOLD_WAITING_EVIDENCE"
                        and review_ready is not False
                    ):
                        errors.append(
                            f"HOLD path must have review_ready=false: {state_key}"
                        )
                        continue

            history = history_by_pair.get(pair, [])
            record = {
                "path_id": path_id,
                "economic_status": "KEEP",
                "economic_judgment": economic,
                "current_event_state": state.get("current_event_state"),
                "last_trigger_key": last_trigger_key,
                "trigger_reason": state.get("last_trigger_reason"),
                "research_state": research_state,
                "research_status": state.get("research_status"),
                "review_ready": review_ready,
                "latest_path_result_id": latest_result_id,
                "latest_path_result_path": latest_result_path,
                "research_history_count": len(history),
            }
            path_records.append(record)
            research_counter[research_state] += 1
            keep_path_count += 1

        bonds.append({
            "bond_code": code,
            "bond_name": name,
            "market_snapshot_id": bond.get(
                "market_snapshot_id",
                registry.get("market_snapshot_id"),
            ),
            "market_cutoff": bond.get(
                "market_cutoff",
                registry.get("market_cutoff"),
            ),
            "keep_path_count": len(path_records),
            "paths": path_records,
        })

    registry_expected_bonds = int(registry.get("bonds_with_any_keep") or 0)
    if expected_bond_count != registry_expected_bonds:
        errors.append(
            "registry internal bond-count mismatch: "
            f"computed={expected_bond_count}, "
            f"declared={registry_expected_bonds}"
        )
    if len(bonds) != expected_bond_count:
        errors.append(
            f"candidate bond-count mismatch: built={len(bonds)}, "
            f"expected={expected_bond_count}"
        )
    if keep_path_count != expected_keep_path_count:
        errors.append(
            f"candidate KEEP path-count mismatch: built={keep_path_count}, "
            f"expected={expected_keep_path_count}"
        )

    status = "PASS" if not errors else "FAIL"
    result = {
        "candidate_pool_version": CANDIDATE_POOL_VERSION,
        "run_id": run_id,
        "unit": "CANDIDATE_POOL",
        "status": status,
        "created_at": _now(),
        "economic_registry_run_id": registry["run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "market_cutoff": registry["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "bond_count": len(bonds),
        "keep_path_count": keep_path_count,
        "research_state_summary": dict(sorted(research_counter.items())),
        "errors": errors,
        "bonds": bonds,
    }

    result_path = run_dir / "candidate_pool.json"
    _write_json(result_path, result)
    _write_json(run_dir / "candidate_pool_audit.json", {
        "status": status,
        "economic_registry_run_id": registry["run_id"],
        "registry_bonds_with_any_keep": registry_expected_bonds,
        "computed_bonds_with_any_keep": expected_bond_count,
        "candidate_bond_count": len(bonds),
        "expected_keep_path_count": expected_keep_path_count,
        "candidate_keep_path_count": keep_path_count,
        "research_state_summary": dict(sorted(research_counter.items())),
        "errors": errors,
    })

    if status == "PASS":
        _write_json(
            data_root / "registry" / "latest_candidate_pool.json",
            {
                "run_id": run_id,
                "status": status,
                "candidate_pool_version": CANDIDATE_POOL_VERSION,
                "economic_registry_run_id": registry["run_id"],
                "market_snapshot_id": registry["market_snapshot_id"],
                "market_cutoff": registry["market_cutoff"],
                "bond_count": len(bonds),
                "keep_path_count": keep_path_count,
                "result_path": str(result_path),
            },
        )

    return result
