"""Conditional contract facts for the ordinary put Path.

The module intentionally applies Decision-Invariance Acquisition: only bonds
with P < 100 require ordinary-put mechanism facts under Put Judgment V1.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from runtime.opportunity.contract_facts import fetch_eastmoney_contract_table

PUT_CONTRACT_FACTS_VERSION = "put-contract-facts-v1"


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def ordinary_put_clause_exists(clause: Any) -> bool:
    text = _text(clause)
    if not text:
        return False

    if re.search(r"有条件回售(?:条款)?", text):
        return True

    structural_markers = (
        "最后两个计息年度" in text
        and "回售" in text
        and ("连续三十个交易日" in text or "连续30个交易日" in text)
    )
    if structural_markers:
        return True

    if "不可由持有人主动回售" in text or "不能由持有人主动回售" in text:
        return False

    return False


def infer_put_mechanism_availability(
    *,
    clause: Any,
    value_date: Any,
    contract_maturity_date: Any,
    market_cutoff: Any,
) -> tuple[bool | None, str]:
    text = _text(clause)
    if not ordinary_put_clause_exists(text):
        return False, "NO_ORDINARY_PUT_CLAUSE"

    value = pd.to_datetime(value_date, errors="coerce")
    maturity = pd.to_datetime(contract_maturity_date, errors="coerce")
    cutoff = pd.to_datetime(market_cutoff, errors="coerce")
    if pd.isna(value) or pd.isna(maturity) or pd.isna(cutoff):
        return None, "MISSING_DATE_FOR_LIFECYCLE_AUDIT"

    if cutoff >= maturity:
        return False, "CONTRACT_MATURITY_REACHED"

    if "最后两个计息年度" not in text:
        return None, "ORDINARY_PUT_WINDOW_NEEDS_SEMANTIC_AUDIT"

    # For a standard six-year convertible, the last two interest years are
    # years 5 and 6. Before the sixth year starts, at least one future annual
    # put window remains even if the current year's right were later consumed.
    term_years = round((maturity - value).days / 365.2425)
    if term_years < 3:
        return None, "NONSTANDARD_TERM_NEEDS_AUDIT"

    final_interest_year_start = value + pd.DateOffset(years=term_years - 1)
    if cutoff < final_interest_year_start:
        return True, "FUTURE_ORDINARY_PUT_WINDOW_REMAINS"

    # In the final interest year, prior exercise / lapse can matter. Do not
    # guess from the static clause; request event evidence.
    return None, "FINAL_INTEREST_YEAR_EVENT_AUDIT_REQUIRED"


def build_put_contract_facts(
    market_input: dict[str, Any],
    eastmoney: pd.DataFrame,
) -> dict[str, Any]:
    required_rows = [
        row for row in market_input["rows"]
        if float(row["current_bond_price"]) < 100.0
    ]
    required_codes = {str(row["bond_code"]).zfill(6) for row in required_rows}

    em = eastmoney.copy()
    em["SECURITY_CODE"] = em["SECURITY_CODE"].astype(str).str.zfill(6)
    em = em[em["SECURITY_CODE"].isin(required_codes)].drop_duplicates("SECURITY_CODE")
    source_rows = em.set_index("SECURITY_CODE").to_dict("index")

    rows = []
    for market_row in required_rows:
        code = str(market_row["bond_code"]).zfill(6)
        source = source_rows.get(code)
        if source is None:
            rows.append({
                "bond_code": code,
                "bond_name": market_row["bond_name"],
                "status": "INSUFFICIENT_DATA",
                "ordinary_put_clause_exists": None,
                "put_mechanism_still_available": None,
                "reason": "CONTRACT_SOURCE_MISSING",
            })
            continue

        clause = source.get("RESALE_CLAUSE")
        exists = ordinary_put_clause_exists(clause)
        available, reason = infer_put_mechanism_availability(
            clause=clause,
            value_date=source.get("VALUE_DATE"),
            contract_maturity_date=source.get("EXPIRE_DATE"),
            market_cutoff=market_input["market_cutoff"],
        )

        status = "READY" if available is not None else "INSUFFICIENT_DATA"
        rows.append({
            "bond_code": code,
            "bond_name": market_row["bond_name"],
            "status": status,
            "ordinary_put_clause_exists": exists,
            "put_mechanism_still_available": available,
            "reason": reason,
            "value_date": _text(source.get("VALUE_DATE")),
            "contract_maturity_date": _text(source.get("EXPIRE_DATE")),
            "resale_clause": _text(clause),
        })

    return {
        "contract_facts_version": PUT_CONTRACT_FACTS_VERSION,
        "market_run_id": market_input["run_id"],
        "market_snapshot_id": market_input["market_snapshot_id"],
        "market_cutoff": market_input["market_cutoff"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "decision_invariance": {
            "price_threshold": 100.0,
            "universe": len(market_input["rows"]),
            "mechanism_audit_required": len(required_rows),
            "mechanism_audit_skipped": len(market_input["rows"]) - len(required_rows),
        },
        "rows": rows,
        "audit": {
            "required": len(required_rows),
            "ready": sum(row["status"] == "READY" for row in rows),
            "insufficient_data": sum(row["status"] == "INSUFFICIENT_DATA" for row in rows),
        },
    }


def run_put_contract_facts(
    market_input_path: Path,
    data_root: Path,
) -> dict[str, Any]:
    market_input = json.loads(market_input_path.read_text(encoding="utf-8"))
    eastmoney = fetch_eastmoney_contract_table()
    return build_put_contract_facts(market_input, eastmoney)
