"""Conditional event audit for maturity-cash Path Availability.

This module is intentionally conservative. Aggregated redemption-status data may
prove that no active early-call blocker is visible or that an announced event is
the normal maturity process. An active early-redemption signal that could replace
normal maturity is escalated instead of being treated as a deterministic DROP.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import akshare as ak
import pandas as pd

AVAILABILITY_VERSION = "maturity-path-availability-v1"


def fetch_redeem_status() -> pd.DataFrame:
    frame = ak.bond_cb_redeem_jsl().copy()
    if frame.empty or "代码" not in frame.columns:
        raise RuntimeError("Jisilu redemption-status table is empty or incomplete")
    frame["代码"] = frame["代码"].astype(str).str.zfill(6)
    return frame


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def infer_availability(
    *,
    redeem_status: Any,
    redeem_counter: Any,
    contract_maturity_date: Any,
    source_maturity_date: Any = None,
) -> tuple[bool | None, str]:
    status = _text(redeem_status)
    counter = _text(redeem_counter)

    if not status or status == "公告不强赎":
        return True, "NO_ACTIVE_EARLY_REDEMPTION_SIGNAL"

    if status == "已公告强赎" and "临近到期" in counter:
        return True, "NORMAL_MATURITY_PROCESS"

    # Some aggregators label maturity repayment as redemption. If the source
    # maturity date and original contract maturity agree, keep the normal path.
    contract_date = pd.to_datetime(contract_maturity_date, errors="coerce")
    source_date = pd.to_datetime(source_maturity_date, errors="coerce")
    if (
        status == "已公告强赎"
        and pd.notna(contract_date)
        and pd.notna(source_date)
        and abs((source_date - contract_date).days) <= 2
    ):
        return True, "REDEMPTION_DATE_MATCHES_CONTRACT_MATURITY"

    if status in {"已公告强赎", "公告要强赎"}:
        return None, "EARLY_REDEMPTION_SIGNAL_REQUIRES_OFFICIAL_AUDIT"

    return None, "UNRECOGNIZED_REDEMPTION_STATUS"


def build_maturity_path_availability(
    candidate_rows: list[dict[str, Any]],
    contract_facts: dict[str, Any],
    redeem_status: pd.DataFrame,
) -> dict[str, Any]:
    candidates = {str(row["bond_code"]).zfill(6): row for row in candidate_rows}
    facts = {str(row["bond_code"]).zfill(6): row for row in contract_facts["rows"]}
    source = redeem_status.drop_duplicates("代码").set_index("代码").to_dict("index")
    rows = []

    for code, candidate in candidates.items():
        fact = facts.get(code, {})
        event = source.get(code)
        if event is None:
            rows.append({
                "bond_code": code,
                "bond_name": candidate.get("bond_name"),
                "status": "INSUFFICIENT_DATA",
                "normal_maturity_path_available": None,
                "reason": "REDEMPTION_STATUS_MISSING",
            })
            continue

        available, reason = infer_availability(
            redeem_status=event.get("强赎状态"),
            redeem_counter=event.get("强赎天计数"),
            contract_maturity_date=fact.get("contract_maturity_date"),
            source_maturity_date=event.get("到期日"),
        )
        rows.append({
            "bond_code": code,
            "bond_name": candidate.get("bond_name"),
            "status": "READY" if available is not None else "INSUFFICIENT_DATA",
            "normal_maturity_path_available": available,
            "reason": reason,
            "redeem_status": _text(event.get("强赎状态")),
            "redeem_counter": _text(event.get("强赎天计数")),
            "last_trade_date": _text(event.get("最后交易日")),
            "source_maturity_date": _text(event.get("到期日")),
        })

    return {
        "availability_version": AVAILABILITY_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_count": len(candidates),
        "rows": rows,
        "audit": {
            "ready": sum(row["status"] == "READY" for row in rows),
            "insufficient_data": sum(row["status"] == "INSUFFICIENT_DATA" for row in rows),
        },
    }
