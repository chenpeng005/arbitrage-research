from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .resolver import EstimatedNavResult


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# R2 main-estimate promotion is deliberately explicit. Shadow cohorts can grow
# without automatically becoming production estimates.
PROMOTED_R2_SOURCES = {
    "R2A_HOLDINGS": {
        "path": "r2a_shadow_snapshot.json",
        "method": "DISCLOSED_HOLDINGS_BASKET",
        "codes": {"501219", "160133", "163110"},
    },
    "R2B2_CASH_HEAVY": {
        "path": "r2b2_cash_shadow/r2b2_cash_shadow.json",
        "method": "R2B2_CASH_HEAVY_HOLDINGS_BASKET",
        "codes": {"160916", "164403", "501077"},
    },
    "R2C_RISK_OVERLAY": {
        "path": "r2c_risk_overlay_shadow.json",
        "method": "RISK_ASSET_OVERLAY",
        "codes": {
            "160621",
            "160641",
            "161019",
            "161216",
            "161713",
            "162215",
            "164105",
            "164606",
            "164902",
            "165509",
            "165517",
            "166105",
        },
    },
}


def _decimal(value) -> Decimal | None:
    try:
        if value is None or value == "":
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed


def _seconds_between(later: datetime, earlier: datetime) -> int:
    if later.tzinfo is None:
        later = later.replace(tzinfo=SHANGHAI_TZ)
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=SHANGHAI_TZ)
    return max(0, int((later - earlier).total_seconds()))


def _load_json(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _quality(row: dict) -> str:
    value = str(row.get("quality_candidate") or "UNKNOWN").upper()
    return value if value in {"HIGH", "MEDIUM", "LOW", "UNKNOWN"} else "UNKNOWN"


def _row_proxy_time(row: dict, snapshot: dict) -> datetime | None:
    # Conservative timestamp: an estimate is only as fresh as the oldest
    # underlying quote that materially entered the basket.
    for key in ("quote_time_min", "proxy_time"):
        parsed = _parse_datetime(row.get(key))
        if parsed is not None:
            return parsed
    return _parse_datetime(snapshot.get("generated_at"))


def _row_is_promotable(
    *,
    source_name: str,
    row: dict,
    expected_method: str,
    expected_anchor_date: date,
) -> bool:
    code = str(row.get("fund_code") or "")
    source = PROMOTED_R2_SOURCES[source_name]
    if code not in source["codes"]:
        return False
    if row.get("method") != expected_method:
        return False
    if row.get("status") not in {"AVAILABLE", "STALE"}:
        return False
    if _decimal(row.get("shadow_estimated_nav")) in (None, Decimal("0")):
        return False
    if str(row.get("official_nav_date") or "") != expected_anchor_date.isoformat():
        return False

    # Keep the first production cohort bounded to the evidence class that was
    # explicitly reviewed. Low-quality shadow rows remain shadow-only.
    if source_name in {"R2A_HOLDINGS", "R2C_RISK_OVERLAY"}:
        if _quality(row) != "MEDIUM":
            return False

    if source_name == "R2B2_CASH_HEAVY":
        backtest = row.get("backtest") or {}
        mae = _decimal(backtest.get("mae_pct"))
        p90 = _decimal(backtest.get("p90_pct"))
        corr = _decimal(backtest.get("corr"))
        if (
            mae is None
            or p90 is None
            or corr is None
            or mae > Decimal("0.35")
            or p90 > Decimal("0.80")
            or corr < Decimal("0.95")
        ):
            return False

    return True


def load_promoted_r2_results(
    *,
    data_root: str | Path,
    expected_anchor_date: date,
    as_of: datetime,
    max_proxy_age_seconds: int = 120,
) -> dict[str, EstimatedNavResult]:
    root = Path(data_root)
    results: dict[str, EstimatedNavResult] = {}

    for source_name, source in PROMOTED_R2_SOURCES.items():
        snapshot = _load_json(root / source["path"])
        if snapshot is None:
            continue

        rows = snapshot.get("rows") or []
        if not isinstance(rows, list):
            continue

        expected_method = str(source["method"])
        for row in rows:
            if not isinstance(row, dict):
                continue
            if not _row_is_promotable(
                source_name=source_name,
                row=row,
                expected_method=expected_method,
                expected_anchor_date=expected_anchor_date,
            ):
                continue

            code = str(row["fund_code"])
            estimated_nav = _decimal(row.get("shadow_estimated_nav"))
            if estimated_nav is None or estimated_nav <= 0:
                continue

            proxy_time = _row_proxy_time(row, snapshot)
            if proxy_time is None:
                continue

            source_status = str(row.get("status"))
            age_seconds = _seconds_between(as_of, proxy_time)
            status = (
                "AVAILABLE"
                if source_status == "AVAILABLE"
                and age_seconds <= max_proxy_age_seconds
                else "STALE"
            )

            results[code] = EstimatedNavResult(
                fund_code=code,
                estimated_nav=estimated_nav,
                estimated_nav_time=proxy_time,
                estimated_nav_status=status,
                estimated_nav_quality=_quality(row),
                resolver_class="R2_DOMESTIC_OTHER",
                resolver_method=expected_method,
                proxy_id=f"PROMOTED_SHADOW:{source_name}",
                proxy_time=proxy_time,
                proxy_return=_decimal(row.get("estimated_return")),
                fx_return=None,
                exposure_ratio_used=(
                    _decimal(row.get("disclosed_stock_weight"))
                    or _decimal(row.get("disclosed_weight"))
                    or _decimal(row.get("total_risk_weight"))
                ),
                tracking_adjustment_used=None,
                error=None,
            )

    return results
