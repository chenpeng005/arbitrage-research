"""Deterministic Research Validity / Reuse Gate.

This module does not discover evidence and does not make investment judgments.
It answers only whether a previous review-ready V2 Path Result may remain bound
to the current Path state after Engineering has supplied state/event validity
signals.
"""

from __future__ import annotations

import json
from typing import Any

REUSE_GATE_VERSION = "research-validity-reuse-gate-v1-experimental"

REUSE_PREVIOUS = "REUSE_PREVIOUS"
SEMANTIC_AUDIT = "SEMANTIC_AUDIT"
FULL_V2_RESEARCH = "FULL_V2_RESEARCH"


def _maturity_band(months: Any) -> str:
    try:
        value = float(months)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if value <= 1:
        return "T_LE_1M"
    if value <= 3:
        return "T_LE_3M"
    if value <= 6:
        return "T_LE_6M"
    if value <= 12:
        return "T_LE_12M"
    return "T_GT_12M"


def _round_number(value: Any, digits: int = 8) -> float | None:
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _contract_cash_signature(fact: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_maturity_date": fact.get("contract_maturity_date"),
        "remaining_contract_cash_C": _round_number(
            fact.get("remaining_contract_cash_C")
        ),
        "remaining_contract_cash_C_min": _round_number(
            fact.get("remaining_contract_cash_C_min")
        ),
        "remaining_contract_cash_C_max": _round_number(
            fact.get("remaining_contract_cash_C_max")
        ),
        "maturity_redemption_cash": _round_number(
            fact.get("maturity_redemption_cash")
        ),
        "coupon_rates": fact.get("coupon_rates"),
        "remaining_intermediate_coupons": fact.get(
            "remaining_intermediate_coupons"
        ),
    }


def research_state_fingerprint(task: dict[str, Any]) -> dict[str, Any]:
    """Return only Path-research-relevant state.

    Ordinary bond/stock price and CV changes are intentionally excluded.
    """
    path_id = str(task.get("path_id") or "")
    market = task.get("market_state") or {}
    trigger = task.get("trigger_context") or {}
    facts = task.get("existing_path_facts") or {}

    if path_id == "MATURITY_CASH":
        contract = facts.get("contract_fact") or {}
        availability = facts.get("path_availability") or {}
        return {
            "path_id": path_id,
            "maturity_band": _maturity_band(market.get("remaining_months")),
            "maturity_date": market.get("maturity_date"),
            "normal_maturity_path_available": availability.get(
                "normal_maturity_path_available"
            ),
            "path_availability_reason": availability.get("reason"),
            "cash_signature": _contract_cash_signature(contract),
        }

    if path_id == "PUT":
        contract = facts.get("contract_fact") or {}
        k = _round_number(market.get("current_conversion_price"))
        return {
            "path_id": path_id,
            "put_window_state": trigger.get("current_event_state"),
            "current_conversion_price": k,
            "put_trigger_line_70pct": (
                round(k * 0.70, 8) if k is not None else None
            ),
            "ordinary_put_clause_exists": contract.get(
                "ordinary_put_clause_exists"
            ),
            "put_mechanism_still_available": contract.get(
                "put_mechanism_still_available"
            ),
            "value_date": contract.get("value_date"),
            "contract_maturity_date": contract.get("contract_maturity_date"),
            # These become active once the incremental PUT counter source exists.
            "put_count_cycle_id": task.get("put_count_cycle_id"),
            "put_count_state": task.get("put_count_state"),
        }

    if path_id == "DOWNWARD_REVISION":
        contract = facts.get("contract_fact") or {}
        return {
            "path_id": path_id,
            "revision_event_state": trigger.get("current_event_state")
            or contract.get("revision_event_state"),
            "current_conversion_price": _round_number(
                market.get("current_conversion_price")
            ),
            "revision_count": contract.get("revision_count"),
            "revision_count_raw": contract.get("revision_count_raw"),
            "minimum_days_needed": contract.get("minimum_days_needed"),
            "reset_start": contract.get("reset_start"),
            "revision_clause_available": contract.get(
                "revision_clause_available"
            ),
            "permanent_revision_blocker": contract.get(
                "permanent_revision_blocker"
            ),
            # Future Event Ledger / cycle identity can fill this explicitly.
            "revision_count_cycle_id": task.get("revision_count_cycle_id"),
        }

    raise ValueError(f"unsupported path_id={path_id!r}")


def _fingerprint_diff(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> list[dict[str, Any]]:
    keys = sorted(set(previous) | set(current))
    return [
        {"field": key, "previous": previous.get(key), "current": current.get(key)}
        for key in keys
        if previous.get(key) != current.get(key)
    ]


def evaluate_research_reuse(
    *,
    previous_task: dict[str, Any] | None,
    previous_result: dict[str, Any] | None,
    current_task: dict[str, Any],
    event_watermark_status: str,
    canonical_compatible: bool | None,
    invalidating_events: list[str] | None = None,
    semantic_audit_events: list[str] | None = None,
    forced_refresh_reason: str | None = None,
) -> dict[str, Any]:
    """Evaluate reuse without fetching evidence or invoking AI.

    event_watermark_status:
      UNCHANGED        Engineering knows no new research-relevant Event exists.
      CHANGED_MATERIAL A known invalidating Event exists.
      CHANGED_SEMANTIC New evidence/event needs semantic classification first.
      UNKNOWN          Engineering cannot yet prove validity.
    """
    invalidating_events = invalidating_events or []
    semantic_audit_events = semantic_audit_events or []

    base = {
        "reuse_gate_version": REUSE_GATE_VERSION,
        "bond_code": str(current_task.get("bond_code") or "").zfill(6),
        "path_id": current_task.get("path_id"),
        "previous_result_id": (
            previous_result.get("path_result_id") if previous_result else None
        ),
        "event_watermark_status": event_watermark_status,
        "canonical_compatible": canonical_compatible,
    }

    if previous_task is None or previous_result is None:
        return {
            **base,
            "research_action": FULL_V2_RESEARCH,
            "reuse_eligible": False,
            "reason": "NO_PREVIOUS_FORMAL_V2_RESULT",
            "fingerprint_diff": [],
        }

    if (
        str(previous_task.get("bond_code") or "").zfill(6) != base["bond_code"]
        or previous_task.get("path_id") != current_task.get("path_id")
    ):
        return {
            **base,
            "research_action": FULL_V2_RESEARCH,
            "reuse_eligible": False,
            "reason": "PREVIOUS_RESULT_IDENTITY_MISMATCH",
            "fingerprint_diff": [],
        }

    if (
        previous_result.get("review_ready") is not True
        or previous_result.get("research_status") != "COMPLETED"
    ):
        return {
            **base,
            "research_action": FULL_V2_RESEARCH,
            "reuse_eligible": False,
            "reason": "PREVIOUS_RESULT_NOT_REVIEW_READY_COMPLETED",
            "fingerprint_diff": [],
        }

    if canonical_compatible is False:
        return {
            **base,
            "research_action": FULL_V2_RESEARCH,
            "reuse_eligible": False,
            "reason": "CANONICAL_OR_CONTRACT_INCOMPATIBLE",
            "fingerprint_diff": [],
        }
    if canonical_compatible is None:
        return {
            **base,
            "research_action": SEMANTIC_AUDIT,
            "reuse_eligible": False,
            "reason": "CANONICAL_COMPATIBILITY_UNKNOWN",
            "fingerprint_diff": [],
        }

    previous_fp = research_state_fingerprint(previous_task)
    current_fp = research_state_fingerprint(current_task)
    diff = _fingerprint_diff(previous_fp, current_fp)

    if forced_refresh_reason:
        return {
            **base,
            "research_action": FULL_V2_RESEARCH,
            "reuse_eligible": False,
            "reason": "PATH_FORCED_REFRESH_NODE",
            "forced_refresh_reason": forced_refresh_reason,
            "previous_fingerprint": previous_fp,
            "current_fingerprint": current_fp,
            "fingerprint_diff": diff,
        }

    if invalidating_events or event_watermark_status == "CHANGED_MATERIAL":
        return {
            **base,
            "research_action": FULL_V2_RESEARCH,
            "reuse_eligible": False,
            "reason": "INVALIDATING_EVENT",
            "invalidating_events": invalidating_events,
            "previous_fingerprint": previous_fp,
            "current_fingerprint": current_fp,
            "fingerprint_diff": diff,
        }

    if semantic_audit_events or event_watermark_status == "CHANGED_SEMANTIC":
        return {
            **base,
            "research_action": SEMANTIC_AUDIT,
            "reuse_eligible": False,
            "reason": "NEW_EVENT_REQUIRES_SEMANTIC_AUDIT",
            "semantic_audit_events": semantic_audit_events,
            "previous_fingerprint": previous_fp,
            "current_fingerprint": current_fp,
            "fingerprint_diff": diff,
        }

    if event_watermark_status == "UNKNOWN":
        return {
            **base,
            "research_action": SEMANTIC_AUDIT,
            "reuse_eligible": False,
            "reason": "EVENT_WATERMARK_UNKNOWN",
            "previous_fingerprint": previous_fp,
            "current_fingerprint": current_fp,
            "fingerprint_diff": diff,
        }

    if event_watermark_status != "UNCHANGED":
        raise ValueError(
            f"unsupported event_watermark_status={event_watermark_status!r}"
        )

    if diff:
        return {
            **base,
            "research_action": FULL_V2_RESEARCH,
            "reuse_eligible": False,
            "reason": "RESEARCH_STATE_FINGERPRINT_CHANGED",
            "previous_fingerprint": previous_fp,
            "current_fingerprint": current_fp,
            "fingerprint_diff": diff,
        }

    return {
        **base,
        "research_action": REUSE_PREVIOUS,
        "reuse_eligible": True,
        "reason": "PREVIOUS_V2_STILL_VALID",
        "bound_result_id": previous_result.get("path_result_id"),
        "previous_fingerprint": previous_fp,
        "current_fingerprint": current_fp,
        "fingerprint_diff": [],
    }


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

[executed on device: iZ2vc3972s0n20m9kq0ns4Z (b3130143-0d28-448b-8a4c-d5f1482304ab)]