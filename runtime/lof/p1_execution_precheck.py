"""P1 execution precheck.

Research-only. This module does not declare an arbitrage opportunity and does
not mutate the main LOF market snapshot. It only exposes execution blockers
that must be cleared before a premium-subscription signal can be called
executable.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from .p1_execution_evidence import load_execution_evidence, match_execution_evidence
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CONTRACT_VERSION = "LOF_P1_EXECUTION_PRECHECK_V0"

SSE_ORDER_INCREMENT = 1.0
SSE_MIN_ORDER_AMOUNT = 1000.0
SZSE_ORDER_INCREMENT = 1.0


def is_qdii_row(row: dict[str, Any]) -> bool:
    lof_type = str(row.get("lof_type") or "").upper()
    fund_type_raw = str(row.get("fund_type_raw") or "").upper()
    resolver_class = str(row.get("resolver_class") or "")
    return (
        lof_type.startswith("QDII_")
        or "QDII" in fund_type_raw
        or resolver_class in {"R3_QDII_INDEX", "R4_QDII_OTHER"}
    )


def _positive_float(value) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _exchange_rules(exchange: str) -> tuple[float, float | None]:
    if exchange == "SSE":
        return SSE_ORDER_INCREMENT, SSE_MIN_ORDER_AMOUNT
    if exchange == "SZSE":
        # SZSE specifies an RMB 1 application unit; the product prospectus
        # supplies the quantity/minimum constraint.
        return SZSE_ORDER_INCREMENT, None
    return 1.0, None


def evaluate_execution_precheck(
    row: dict[str, Any],
    *,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    code = str(row.get("code") or "")
    exchange = str(row.get("exchange") or "")
    status = str(row.get("subscription_status") or "UNKNOWN")
    increment, exchange_min = _exchange_rules(exchange)

    fund_min = _positive_float(row.get("minimum_subscription_amount"))
    if exchange_min is not None and fund_min is not None:
        effective_min = max(exchange_min, fund_min)
    elif exchange_min is not None:
        effective_min = exchange_min
    else:
        effective_min = fund_min

    limit_amount = (
        _positive_float(row.get("daily_subscription_limit"))
        if status == "LIMITED"
        else None
    )
    limit_conflict = bool(
        status == "LIMITED"
        and limit_amount is not None
        and effective_min is not None
        and limit_amount < effective_min
    )

    confirmation_days = row.get("subscription_confirmation_days")
    sell_days = row.get("subscription_to_sell_days")
    evidence_status, matched_evidence = match_execution_evidence(row, evidence)
    limit_scope = str(row.get("limit_scope") or "UNKNOWN")
    over_limit_handling = "UNKNOWN"
    sellable_timing_evidence_level = "UNVERIFIED"
    evidence_date = None
    evidence_source_url = None
    evidence_source_authority = None
    if evidence_status == "MATCHED" and matched_evidence is not None:
        limit_scope = str(
            matched_evidence.get("limit_scope")
            or limit_scope
        )
        over_limit_handling = str(
            matched_evidence.get("over_limit_handling")
            or "UNKNOWN"
        )
        sellable_timing_evidence_level = str(
            matched_evidence.get("sellable_timing_evidence_level")
            or "UNVERIFIED"
        )
        evidence_date = matched_evidence.get("evidence_date")
        evidence_source_url = matched_evidence.get("source_url")
        evidence_source_authority = matched_evidence.get("source_authority")

    blockers: list[str] = []

    if status == "SUSPENDED":
        blockers.append("SUBSCRIPTION_SUSPENDED")
        state = "BLOCKED"
    elif status not in {"OPEN", "LIMITED"}:
        blockers.append("SUBSCRIPTION_STATUS_UNKNOWN")
        state = "BLOCKED"
    else:
        if effective_min is None:
            blockers.append("EFFECTIVE_MIN_ORDER_AMOUNT_UNKNOWN")
        if status == "LIMITED" and limit_amount is None:
            blockers.append("LIMIT_AMOUNT_UNKNOWN")
        if status == "LIMITED" and limit_scope == "UNKNOWN":
            blockers.append("LIMIT_SCOPE_UNKNOWN")
        if limit_conflict:
            if over_limit_handling != "PARTIAL_CONFIRM_TO_LIMIT":
                blockers.append(
                    "OVER_LIMIT_PARTIAL_CONFIRMATION_UNVERIFIED"
                )
            blockers.append("BROKER_OVER_LIMIT_SUPPORT_UNVERIFIED")
        if confirmation_days is None:
            blockers.append("CONFIRMATION_DAYS_UNKNOWN")
        if is_qdii_row(row) and sell_days is None:
            blockers.append("SELLABLE_TIMING_UNKNOWN_QDII")
        elif sell_days is None:
            blockers.append("SELLABLE_TIMING_UNKNOWN")
        state = "INCOMPLETE" if blockers else "PRECHECK_CLEAR"

    return {
        "code": code,
        "name": row.get("name"),
        "exchange": exchange,
        "lof_type": row.get("lof_type"),
        "fund_type_raw": row.get("fund_type_raw"),
        "resolver_class": row.get("resolver_class"),
        "subscription_status": status,
        "exchange_order_increment": increment,
        "exchange_min_order_amount": exchange_min,
        "fund_min_subscription_amount": fund_min,
        "effective_min_order_amount": effective_min,
        "daily_subscription_limit": limit_amount,
        "limit_scope": limit_scope,
        "limit_vs_order_min_conflict": limit_conflict,
        "requires_over_limit_partial_confirmation": limit_conflict,
        "over_limit_handling": over_limit_handling,
        "fund_partial_confirmation_confirmed": (
            over_limit_handling == "PARTIAL_CONFIRM_TO_LIMIT"
        ),
        "execution_evidence_status": evidence_status,
        "execution_evidence_date": evidence_date,
        "execution_evidence_source_authority": evidence_source_authority,
        "execution_evidence_source_url": evidence_source_url,
        "sellable_timing_evidence_level": sellable_timing_evidence_level,
        "subscription_confirmation_days": confirmation_days,
        "subscription_to_sell_days": sell_days,
        "qdii_timing_evidence_required": bool(
            is_qdii_row(row) and sell_days is None
        ),
        "execution_precheck_state": state,
        "blockers": blockers,
        "eligible_for_opportunity": False,
    }


def build_execution_precheck_snapshot(
    *,
    main_snapshot: dict[str, Any],
    generated_at: datetime,
    evidence_rows: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    evidence_rows = (
        load_execution_evidence()
        if evidence_rows is None
        else evidence_rows
    )
    rows = [
        evaluate_execution_precheck(
            row,
            evidence=evidence_rows.get(str(row.get("code") or "")),
        )
        for row in (main_snapshot.get("rows") or [])
        if is_qdii_row(row)
    ]
    rows.sort(key=lambda row: (row["exchange"], row["code"]))

    summary = {
        "row_count": len(rows),
        "open_count": sum(
            row["subscription_status"] == "OPEN" for row in rows
        ),
        "limited_count": sum(
            row["subscription_status"] == "LIMITED" for row in rows
        ),
        "suspended_count": sum(
            row["subscription_status"] == "SUSPENDED" for row in rows
        ),
        "blocked_count": sum(
            row["execution_precheck_state"] == "BLOCKED" for row in rows
        ),
        "incomplete_count": sum(
            row["execution_precheck_state"] == "INCOMPLETE" for row in rows
        ),
        "precheck_clear_count": sum(
            row["execution_precheck_state"] == "PRECHECK_CLEAR"
            for row in rows
        ),
        "order_limit_conflict_count": sum(
            bool(row["limit_vs_order_min_conflict"]) for row in rows
        ),
        "sellable_timing_unknown_count": sum(
            bool(row["qdii_timing_evidence_required"]) for row in rows
        ),
        "execution_evidence_matched_count": sum(
            row["execution_evidence_status"] == "MATCHED"
            for row in rows
        ),
        "fund_partial_confirmation_confirmed_count": sum(
            bool(row["fund_partial_confirmation_confirmed"])
            for row in rows
        ),
    }

    semantic = {
        "contract_version": CONTRACT_VERSION,
        "rows": rows,
        "summary": summary,
    }
    digest = hashlib.sha256(
        json.dumps(
            semantic,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "semantic_hash": digest,
        "summary": summary,
        "rows": rows,
    }


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def persist_execution_precheck(
    data_root: str | Path,
    snapshot: dict[str, Any],
) -> tuple[Path, Path | None]:
    root = Path(data_root)
    latest = root / "p1_execution_precheck.json"
    prior_hash = None
    if latest.is_file():
        try:
            prior_hash = json.loads(
                latest.read_text(encoding="utf-8")
            ).get("semantic_hash")
        except (OSError, json.JSONDecodeError):
            prior_hash = None

    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"
    _atomic_write(latest, payload)

    archive = None
    if prior_hash != snapshot.get("semantic_hash"):
        stamp = str(snapshot["generated_at"]).replace(":", "").replace("+", "_")
        archive = (
            root
            / "p1_execution_precheck_history"
            / f"{stamp}-{str(snapshot['semantic_hash'])[:10]}.json"
        )
        _atomic_write(archive, payload)

    return latest, archive


def run_once(
    *,
    data_root: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")
    snapshot = build_execution_precheck_snapshot(
        main_snapshot=main_snapshot,
        generated_at=now,
    )
    latest, archive = persist_execution_precheck(data_root, snapshot)
    return {
        "snapshot": snapshot,
        "latest_path": str(latest),
        "archive_path": str(archive) if archive is not None else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    args = parser.parse_args(argv)
    result = run_once(data_root=args.data_root)
    snapshot = result["snapshot"]
    print(
        json.dumps(
            {
                "status": "PASS",
                "generated_at": snapshot["generated_at"],
                "source_market_snapshot_id": snapshot[
                    "source_market_snapshot_id"
                ],
                "summary": snapshot["summary"],
                "latest_path": result["latest_path"],
                "archive_path": result["archive_path"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
