"""Engineering Research Trigger V1.

Consumes an Economic Path Registry and existing structured Path event facts.
The trigger is stateful and edge-triggered: identical states do not emit the
same research task repeatedly.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

TRIGGER_VERSION = "engineering-research-trigger-v1"
ACTIVE_PATHS = ("MATURITY_CASH", "PUT", "DOWNWARD_REVISION")
REVISION_TRIGGER_STATES = {
    "临近触发": "REVISION_EVENT_NEAR_TRIGGER",
    "满足条件": "REVISION_EVENT_CONDITION_MET",
    "待股东会": "REVISION_EVENT_SHAREHOLDER_MEETING_PENDING",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _path_key(code: str, path_id: str) -> str:
    return f"{str(code).zfill(6)}:{path_id}"


def _maturity_band(months: float) -> tuple[str, int]:
    value = float(months)
    if value <= 1:
        return "T_LE_1M", 4
    if value <= 3:
        return "T_LE_3M", 3
    if value <= 6:
        return "T_LE_6M", 2
    if value <= 12:
        return "T_LE_12M", 1
    return "T_GT_12M", 0


def _maturity_band_reason(band: str) -> str | None:
    return {
        "T_LE_12M": "MATURITY_T_MINUS_12M",
        "T_LE_6M": "MATURITY_T_MINUS_6M",
        "T_LE_3M": "MATURITY_T_MINUS_3M",
        "T_LE_1M": "MATURITY_T_MINUS_1M",
    }.get(band)


def _put_event_state(fact: dict[str, Any] | None, market_cutoff: str) -> str:
    if not fact:
        return "PUT_FACTS_UNAVAILABLE"
    if not fact.get("ordinary_put_clause_exists"):
        return "NO_ORDINARY_PUT"
    value_date = pd.to_datetime(fact.get("value_date"), errors="coerce")
    maturity_date = pd.to_datetime(fact.get("contract_maturity_date"), errors="coerce")
    cutoff = pd.to_datetime(market_cutoff, errors="coerce")
    if pd.isna(value_date) or pd.isna(maturity_date) or pd.isna(cutoff):
        return "PUT_WINDOW_UNKNOWN"
    term_years = round((maturity_date - value_date).days / 365.2425)
    if term_years < 3:
        return "PUT_WINDOW_UNKNOWN"
    window_start = value_date + pd.DateOffset(years=term_years - 2)
    return "IN_PUT_WINDOW" if cutoff >= window_start else "BEFORE_PUT_WINDOW"


def _load_child_facts(
    data_root: Path,
    registry: dict[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    put_run_id = registry["path_summary"]["PUT"]["child_run_id"]
    revision_run_id = registry["path_summary"]["DOWNWARD_REVISION"]["child_run_id"]

    put_path = data_root / "runs" / put_run_id / "put_contract_facts.json"
    revision_path = (
        data_root / "runs" / revision_run_id / "revision_contract_facts.json"
    )

    put_facts = _read_json(put_path)
    revision_facts = _read_json(revision_path)

    return (
        {str(row["bond_code"]).zfill(6): row for row in put_facts["rows"]},
        {str(row["bond_code"]).zfill(6): row for row in revision_facts["rows"]},
    )


def _new_keep_episode(path_id: str, market_snapshot_id: str) -> str:
    return f"{path_id}:{market_snapshot_id}"


def run_research_trigger(
    economic_registry_path: Path,
    data_root: Path,
    deployment: dict[str, Any],
) -> dict[str, Any]:
    registry = _read_json(economic_registry_path)
    run_id = (
        datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        + "_trigger_"
        + uuid.uuid4().hex[:8]
    )
    run_dir = data_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    started_at = _now()

    market_input_path = (
        data_root
        / "runs"
        / registry["market_run_id"]
        / "discovery_market_input.json"
    )
    market_input = _read_json(market_input_path)
    market_rows = {
        str(row["bond_code"]).zfill(6): row for row in market_input["rows"]
    }

    put_facts, revision_facts = _load_child_facts(data_root, registry)

    state_path = data_root / "registry" / "research_trigger_state.json"
    previous_state = _read_json(state_path) if state_path.exists() else {
        "trigger_version": TRIGGER_VERSION,
        "paths": {},
    }
    previous_paths = previous_state.get("paths", {})

    next_paths: dict[str, dict[str, Any]] = {}
    outputs: list[dict[str, Any]] = []
    tasks: list[dict[str, Any]] = []

    for bond in registry["bonds"]:
        code = str(bond["bond_code"]).zfill(6)
        market_row = market_rows[code]

        for path_id in ACTIVE_PATHS:
            path_result = bond["paths"][path_id]
            economic_status = path_result.get("economic_status")
            key = _path_key(code, path_id)
            prev = previous_paths.get(key)

            if economic_status != "KEEP":
                next_paths[key] = {
                    "bond_code": code,
                    "bond_name": bond["bond_name"],
                    "path_id": path_id,
                    "economic_status": economic_status,
                    "keep_episode_id": None,
                    "current_event_state": None,
                    "previous_event_state": prev.get("current_event_state") if prev else None,
                    "current_market_snapshot_id": registry["market_snapshot_id"],
                    "current_conversion_price": float(market_row["current_conversion_price"]),
                    "last_trigger_key": prev.get("last_trigger_key") if prev else None,
                    "last_triggered_at": prev.get("last_triggered_at") if prev else None,
                    "research_status": prev.get("research_status") if prev else None,
                    "last_path_result_id": prev.get("last_path_result_id") if prev else None,
                    "updated_at": _now(),
                }
                continue

            new_episode = prev is None or prev.get("economic_status") != "KEEP"
            keep_episode_id = (
                _new_keep_episode(path_id, registry["market_snapshot_id"])
                if new_episode
                else prev["keep_episode_id"]
            )

            trigger_reason: str | None = None
            trigger_key: str | None = None
            current_event_state: str | None = None
            extra: dict[str, Any] = {}

            if path_id == "MATURITY_CASH":
                band, band_rank = _maturity_band(float(market_row["remaining_months"]))
                current_event_state = band
                extra["remaining_months"] = float(market_row["remaining_months"])
                extra["maturity_date"] = market_row.get("maturity_date")

                if new_episode:
                    trigger_reason = "MATURITY_KEEP_ENTRY"
                    trigger_key = f"MATURITY_KEEP_ENTRY:{keep_episode_id}"
                else:
                    previous_band = prev.get("current_event_state")
                    _, previous_rank = _maturity_band(
                        float(prev.get("remaining_months", 9999.0))
                    )
                    if band_rank > previous_rank:
                        trigger_reason = _maturity_band_reason(band)
                        if trigger_reason:
                            trigger_key = f"{trigger_reason}:{keep_episode_id}"

            elif path_id == "PUT":
                fact = put_facts.get(code)
                current_event_state = _put_event_state(
                    fact,
                    registry["market_cutoff"],
                )
                if fact:
                    extra["put_contract_reason"] = fact.get("reason")
                    extra["put_value_date"] = fact.get("value_date")
                    extra["put_contract_maturity_date"] = fact.get(
                        "contract_maturity_date"
                    )

                if new_episode:
                    trigger_reason = "PUT_KEEP_ENTRY"
                    trigger_key = f"PUT_KEEP_ENTRY:{keep_episode_id}"
                elif (
                    current_event_state == "IN_PUT_WINDOW"
                    and prev.get("current_event_state") != "IN_PUT_WINDOW"
                ):
                    trigger_reason = "PUT_WINDOW_ENTERED"
                    trigger_key = f"PUT_WINDOW_ENTERED:{keep_episode_id}"

            elif path_id == "DOWNWARD_REVISION":
                fact = revision_facts.get(code, {})
                current_event_state = fact.get("revision_event_state") or "UNKNOWN"
                extra["revision_count"] = fact.get("revision_count")
                extra["minimum_days_needed"] = fact.get("minimum_days_needed")
                extra["reset_start"] = fact.get("reset_start")

                previous_k = (
                    float(prev["current_conversion_price"])
                    if prev and prev.get("current_conversion_price") is not None
                    else None
                )
                current_k = float(market_row["current_conversion_price"])

                if previous_k is not None and current_k < previous_k - 1e-9:
                    trigger_reason = "REVISION_PRICE_CHANGED"
                    trigger_key = (
                        f"REVISION_PRICE_CHANGED:{keep_episode_id}:"
                        f"{previous_k:.6f}->{current_k:.6f}:"
                        f"{registry['market_snapshot_id']}"
                    )
                elif (
                    current_event_state in REVISION_TRIGGER_STATES
                    and (new_episode or prev.get("current_event_state") != current_event_state)
                ):
                    trigger_reason = REVISION_TRIGGER_STATES[current_event_state]
                    trigger_key = (
                        f"{trigger_reason}:{keep_episode_id}:"
                        f"{registry['market_snapshot_id']}"
                    )

            last_trigger_key = prev.get("last_trigger_key") if prev else None
            already_emitted = trigger_key is not None and trigger_key == last_trigger_key
            research_trigger = trigger_key is not None and not already_emitted

            if research_trigger:
                last_trigger_key = trigger_key
                last_triggered_at = _now()
                research_status = "PENDING"
                trigger_status = "EMITTED_NEW"
            else:
                last_triggered_at = prev.get("last_triggered_at") if prev else None
                research_status = prev.get("research_status") if prev else None
                trigger_status = (
                    "ALREADY_EMITTED"
                    if already_emitted
                    else "NOT_TRIGGERED"
                )

            state_row = {
                "bond_code": code,
                "bond_name": bond["bond_name"],
                "path_id": path_id,
                "economic_status": "KEEP",
                "keep_episode_id": keep_episode_id,
                "current_event_state": current_event_state,
                "previous_event_state": prev.get("current_event_state") if prev else None,
                "current_market_snapshot_id": registry["market_snapshot_id"],
                "current_conversion_price": float(market_row["current_conversion_price"]),
                "last_trigger_key": last_trigger_key,
                "last_triggered_at": last_triggered_at,
                "research_status": research_status,
                "last_path_result_id": prev.get("last_path_result_id") if prev else None,
                "updated_at": _now(),
                **extra,
            }
            next_paths[key] = state_row

            output = {
                "bond_code": code,
                "bond_name": bond["bond_name"],
                "path_id": path_id,
                "economic_status": "KEEP",
                "research_trigger": research_trigger,
                "trigger_reason": trigger_reason if research_trigger else None,
                "trigger_key": trigger_key if research_trigger else None,
                "trigger_status": trigger_status,
                "current_event_state": current_event_state,
                "keep_episode_id": keep_episode_id,
                **extra,
            }
            outputs.append(output)
            if research_trigger:
                tasks.append(output)

    state = {
        "trigger_version": TRIGGER_VERSION,
        "economic_registry_run_id": registry["run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "updated_at": _now(),
        "paths": next_paths,
    }
    _write_json(state_path, state)

    reason_counts: dict[str, int] = {}
    for task in tasks:
        reason = str(task["trigger_reason"])
        reason_counts[reason] = reason_counts.get(reason, 0) + 1

    result = {
        "run_id": run_id,
        "unit": "ENGINEERING_RESEARCH_TRIGGER",
        "trigger_version": TRIGGER_VERSION,
        "status": "PASS",
        "started_at": started_at,
        "completed_at": _now(),
        "economic_registry_run_id": registry["run_id"],
        "market_run_id": registry["market_run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "market_cutoff": registry["market_cutoff"],
        "application_commit_sha": deployment.get("application_commit_sha"),
        "knowledge_commit_sha": deployment.get("knowledge_commit_sha"),
        "economic_keep_paths": len(outputs),
        "triggered_tasks": len(tasks),
        "trigger_reason_counts": reason_counts,
        "tasks": tasks,
        "paths": outputs,
    }

    _write_json(run_dir / "research_trigger_result.json", result)
    _write_json(run_dir / "research_trigger_tasks.json", {
        "run_id": run_id,
        "tasks": tasks,
    })
    _write_json(run_dir / "run_metadata.json", {
        "run_id": run_id,
        "unit": "ENGINEERING_RESEARCH_TRIGGER",
        "trigger_version": TRIGGER_VERSION,
        "status": "PASS",
        "started_at": started_at,
        "completed_at": result["completed_at"],
        "economic_registry_run_id": registry["run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "application_commit_sha": result["application_commit_sha"],
        "knowledge_commit_sha": result["knowledge_commit_sha"],
        "artifacts": {
            "trigger_result": str(run_dir / "research_trigger_result.json"),
            "trigger_tasks": str(run_dir / "research_trigger_tasks.json"),
            "trigger_state": str(state_path),
        },
    })

    latest = data_root / "registry" / "latest_research_trigger.json"
    _write_json(latest, {
        "run_id": run_id,
        "economic_registry_run_id": registry["run_id"],
        "market_snapshot_id": registry["market_snapshot_id"],
        "status": "PASS",
        "economic_keep_paths": len(outputs),
        "triggered_tasks": len(tasks),
        "result_path": str(run_dir / "research_trigger_result.json"),
        "tasks_path": str(run_dir / "research_trigger_tasks.json"),
    })
    return result
