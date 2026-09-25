"""Maturity-cash Path Economic Judgment V1."""

from __future__ import annotations

from typing import Any

JUDGMENT_VERSION = "maturity-cash-economic-judgment-v1"


def judge_one(
    *,
    current_price: float,
    contract_fact: dict[str, Any],
    normal_maturity_path_available: bool | None = None,
) -> dict[str, Any]:
    if contract_fact.get("status") not in {"READY", "READY_WITH_OVERRIDE"}:
        return {
            "economic_status": "INSUFFICIENT_DATA",
            "reason": "CONTRACT_FACTS_INSUFFICIENT",
        }

    c_value = float(contract_fact["remaining_contract_cash_C"])
    p_value = float(current_price)
    spread = c_value - p_value

    if spread <= 0:
        return {
            "economic_status": "DROP",
            "reason": "NO_POSITIVE_CASH_SPREAD",
            "current_price_P": p_value,
            "remaining_contract_cash_C": c_value,
            "spread_C_minus_P": spread,
            "path_availability_required": False,
        }

    if normal_maturity_path_available is None:
        return {
            "economic_status": "INSUFFICIENT_DATA",
            "reason": "NORMAL_MATURITY_PATH_AVAILABILITY_REQUIRED",
            "current_price_P": p_value,
            "remaining_contract_cash_C": c_value,
            "spread_C_minus_P": spread,
            "path_availability_required": True,
        }

    if not normal_maturity_path_available:
        return {
            "economic_status": "DROP",
            "reason": "NORMAL_MATURITY_PATH_UNAVAILABLE",
            "current_price_P": p_value,
            "remaining_contract_cash_C": c_value,
            "spread_C_minus_P": spread,
            "path_availability_required": True,
        }

    return {
        "economic_status": "KEEP",
        "reason": "POSITIVE_MATURITY_CASH_SPREAD",
        "current_price_P": p_value,
        "remaining_contract_cash_C": c_value,
        "spread_C_minus_P": spread,
        "path_availability_required": True,
    }


def judge_market(
    market_input: dict[str, Any],
    contract_facts: dict[str, Any],
    *,
    availability_by_code: dict[str, bool | None] | None = None,
) -> dict[str, Any]:
    availability_by_code = availability_by_code or {}
    facts = {str(row["bond_code"]).zfill(6): row for row in contract_facts["rows"]}
    rows = []

    for market_row in market_input["rows"]:
        code = str(market_row["bond_code"]).zfill(6)
        fact = facts.get(code, {"status": "INSUFFICIENT_DATA"})
        rows.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            **judge_one(
                current_price=float(market_row["current_bond_price"]),
                contract_fact=fact,
                normal_maturity_path_available=availability_by_code.get(code),
            ),
        })

    return {
        "judgment_version": JUDGMENT_VERSION,
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "contract_facts_version": contract_facts["contract_facts_version"],
        "rows": rows,
        "summary": {
            "KEEP": sum(row["economic_status"] == "KEEP" for row in rows),
            "DROP": sum(row["economic_status"] == "DROP" for row in rows),
            "INSUFFICIENT_DATA": sum(row["economic_status"] == "INSUFFICIENT_DATA" for row in rows),
        },
    }
