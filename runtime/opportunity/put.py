"""Ordinary put Path Economic Judgment V1."""

from __future__ import annotations

from typing import Any

PUT_REFERENCE = 100.0
JUDGMENT_VERSION = "put-economic-judgment-v1"


def judge_one(
    *,
    current_price: float,
    put_mechanism_still_available: bool | None = None,
) -> dict[str, Any]:
    p_value = float(current_price)
    spread = PUT_REFERENCE - p_value

    if p_value >= PUT_REFERENCE:
        return {
            "economic_status": "DROP",
            "reason": "NO_POSITIVE_PUT_SPREAD",
            "current_price_P": p_value,
            "put_reference": PUT_REFERENCE,
            "spread": spread,
            "mechanism_audit_required": False,
        }

    if put_mechanism_still_available is None:
        return {
            "economic_status": "INSUFFICIENT_DATA",
            "reason": "PUT_MECHANISM_AVAILABILITY_REQUIRED",
            "current_price_P": p_value,
            "put_reference": PUT_REFERENCE,
            "spread": spread,
            "mechanism_audit_required": True,
        }

    if not put_mechanism_still_available:
        return {
            "economic_status": "DROP",
            "reason": "PUT_MECHANISM_UNAVAILABLE",
            "current_price_P": p_value,
            "put_reference": PUT_REFERENCE,
            "spread": spread,
            "mechanism_audit_required": True,
        }

    return {
        "economic_status": "KEEP",
        "reason": "POSITIVE_PUT_SPREAD",
        "current_price_P": p_value,
        "put_reference": PUT_REFERENCE,
        "spread": spread,
        "mechanism_audit_required": True,
    }


def judge_market(
    market_input: dict[str, Any],
    *,
    availability_by_code: dict[str, bool | None] | None = None,
) -> dict[str, Any]:
    availability_by_code = availability_by_code or {}
    rows = []

    for market_row in market_input["rows"]:
        code = str(market_row["bond_code"]).zfill(6)
        rows.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            **judge_one(
                current_price=float(market_row["current_bond_price"]),
                put_mechanism_still_available=availability_by_code.get(code),
            ),
        })

    return {
        "judgment_version": JUDGMENT_VERSION,
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "rows": rows,
        "summary": {
            "KEEP": sum(row["economic_status"] == "KEEP" for row in rows),
            "DROP": sum(row["economic_status"] == "DROP" for row in rows),
            "INSUFFICIENT_DATA": sum(row["economic_status"] == "INSUFFICIENT_DATA" for row in rows),
            "mechanism_audit_required": sum(row["mechanism_audit_required"] for row in rows),
        },
    }
