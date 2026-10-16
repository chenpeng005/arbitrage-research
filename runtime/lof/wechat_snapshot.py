from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

from .snapshot_store import snapshot_to_json


CONTRACT_VERSION = "wechat-upstream-snapshot-v0.1"
SOURCE_OWNER = "LOF_RUNTIME"
ELIGIBLE_SUBSCRIPTION_STATES = {"OPEN", "LIMITED"}
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=SHANGHAI_TZ)
    return result.astimezone(SHANGHAI_TZ)


def _iso(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value or "").strip()
    return text or None


def _source_hash(snapshot: dict) -> str:
    return sha256(snapshot_to_json(snapshot).encode("utf-8")).hexdigest()


def _latest_quote_date(rows: list[dict]) -> date | None:
    dates: list[date] = []
    for row in rows:
        quote_time = _parse_datetime(row.get("quote_time"))
        amount = _as_float(row.get("amount"))
        if quote_time is not None and amount is not None and amount > 0:
            dates.append(quote_time.date())
    return max(dates) if dates else None


def _eligibility_blockers(row: dict, *, logical_date: date | None) -> list[str]:
    blockers: list[str] = []
    quote_time = _parse_datetime(row.get("quote_time"))
    price = _as_float(row.get("price"))
    amount = _as_float(row.get("amount"))
    official_nav = _as_float(row.get("official_nav"))
    premium = _as_float(row.get("static_premium_rate"))
    nav_date = _iso(row.get("official_nav_date"))
    subscription_status = str(row.get("subscription_status") or "").upper()
    limit_type = str(row.get("daily_subscription_limit_type") or "").upper()
    limit_amount = _as_float(row.get("daily_subscription_limit"))

    if subscription_status not in ELIGIBLE_SUBSCRIPTION_STATES:
        blockers.append("SUBSCRIPTION_NOT_OPEN")
    if quote_time is None or logical_date is None or quote_time.date() != logical_date:
        blockers.append("QUOTE_NOT_LATEST_TRADE_DATE")
    if price is None or price <= 0:
        blockers.append("PRICE_MISSING")
    if amount is None or amount <= 0:
        blockers.append("TURNOVER_NOT_POSITIVE")
    if str(row.get("official_nav_status") or "").upper() != "AVAILABLE":
        blockers.append("OFFICIAL_NAV_UNAVAILABLE")
    if official_nav is None or official_nav <= 0:
        blockers.append("OFFICIAL_NAV_MISSING")
    if not nav_date:
        blockers.append("OFFICIAL_NAV_DATE_MISSING")
    if premium is None:
        blockers.append("OFFICIAL_PREMIUM_MISSING")
    if limit_type in {"", "UNKNOWN", "NOT_APPLICABLE"}:
        blockers.append("SUBSCRIPTION_LIMIT_UNKNOWN")
    elif limit_type == "NUMERIC" and limit_amount is None:
        blockers.append("SUBSCRIPTION_LIMIT_MISSING")

    return blockers


def build_lof_wechat_snapshot(
    snapshot: dict,
    *,
    generated_at: datetime | None = None,
    ranking_limit: int = 5,
) -> dict:
    if ranking_limit <= 0:
        raise ValueError("ranking_limit must be positive")

    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise ValueError("source snapshot rows must be a list")

    logical_date = _latest_quote_date(rows)
    source_generated_at = _iso(snapshot.get("generated_at"))
    source_cutoff = _iso(snapshot.get("market_cutoff"))
    output_generated_at = generated_at or _parse_datetime(snapshot.get("generated_at"))
    if output_generated_at is None:
        output_generated_at = datetime.now(SHANGHAI_TZ)

    projected: list[dict] = []
    for source_row in rows:
        blockers = _eligibility_blockers(source_row, logical_date=logical_date)
        quote_time = _parse_datetime(source_row.get("quote_time"))
        amount = _as_float(source_row.get("amount"))
        row = {
            "code": str(source_row.get("code") or ""),
            "name": str(source_row.get("name") or ""),
            "exchange": str(source_row.get("exchange") or ""),
            "market_price": _as_float(source_row.get("price")),
            "quote_time": quote_time.isoformat() if quote_time is not None else None,
            "official_nav": _as_float(source_row.get("official_nav")),
            "official_nav_date": _iso(source_row.get("official_nav_date")),
            "official_nav_lag_label": source_row.get("official_nav_lag_label"),
            "official_premium_rate": _as_float(source_row.get("static_premium_rate")),
            "subscription_status": str(source_row.get("subscription_status") or "UNKNOWN").upper(),
            "subscription_limit_cny": _as_float(source_row.get("daily_subscription_limit")),
            "subscription_limit_type": str(source_row.get("daily_subscription_limit_type") or "UNKNOWN").upper(),
            "subscription_limit_raw": source_row.get("daily_subscription_limit_raw"),
            "turnover_cny": amount,
            "has_real_turnover": (amount or 0.0) > 0,
            "eligible_for_wechat_table": not blockers,
            "eligibility_blockers": blockers,
        }
        projected.append(row)

    eligible = [row for row in projected if row["eligible_for_wechat_table"]]
    eligible.sort(
        key=lambda row: (
            -float(row["official_premium_rate"]),
            row["code"],
        )
    )
    display_rows = eligible[:ranking_limit]
    status = "READY" if len(display_rows) == ranking_limit else "BLOCKED"
    block_reason = None if status == "READY" else "INSUFFICIENT_ELIGIBLE_ROWS"

    return {
        "contract_version": CONTRACT_VERSION,
        "source_owner": SOURCE_OWNER,
        "logical_date": logical_date.isoformat() if logical_date else None,
        "generated_at": output_generated_at.isoformat(),
        "content_cutoff_at": source_cutoff,
        "source_snapshot_id": snapshot.get("snapshot_id"),
        "source_snapshot_sha256": _source_hash(snapshot),
        "source_snapshot_generated_at": source_generated_at,
        "status": status,
        "block_reason": block_reason,
        "ranking_limit": ranking_limit,
        "evaluated_count": len(projected),
        "eligible_count": len(eligible),
        "rows": display_rows,
        "delta_items": [],
        "critical_empty_fields": [] if logical_date is not None else ["logical_date"],
    }


class LofWechatSnapshotStore:
    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)
        self.root = self.data_root / "wechat_snapshot"
        self.daily_dir = self.root / "daily"
        self.latest_path = self.root / "latest.json"

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def persist(self, payload: dict) -> Path:
        logical_date = str(payload.get("logical_date") or "").strip()
        if not logical_date:
            raise ValueError("logical_date is required")
        json_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        daily_path = self.daily_dir / f"{logical_date}.json"
        self._atomic_write(daily_path, json_text)
        self._atomic_write(self.latest_path, json_text)
        return daily_path

    def build_and_persist(
        self,
        source_snapshot: dict,
        *,
        generated_at: datetime | None = None,
        ranking_limit: int = 5,
    ) -> tuple[dict, Path]:
        payload = build_lof_wechat_snapshot(
            source_snapshot,
            generated_at=generated_at,
            ranking_limit=ranking_limit,
        )
        path = self.persist(payload)
        return payload, path
