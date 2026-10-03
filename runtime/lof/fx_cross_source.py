from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
import math
import statistics
from urllib.parse import urlencode

from .fx import FxDailyClose, FxQuote, fx_close_on
from .http_json import fetch_json_with_retry
from .wscn_market_proxy import WscnMarketProxyQuote


WSCN_MARKET_KLINE_URL = "https://api-ddc-wscn.awtmt.com/market/kline"
WSCN_USDCNY = "USDCNY.OTC"
TENCENT_USDCNY = "whUSDCNY"


@dataclass(frozen=True)
class FxCrossSourceCalibration:
    status: str
    common_count: int
    common_start: date | None
    common_end: date | None
    median_abs_level_diff_bps: float | None
    p90_abs_level_diff_bps: float | None
    max_abs_level_diff_bps: float | None
    error: str | None = None


@dataclass(frozen=True)
class FxCrossSourceBridge:
    status: str
    anchor_date: date
    anchor: Decimal | None
    current: Decimal | None
    quote_time: datetime | None
    source: str
    fx_return: Decimal | None
    quote_age_seconds: int | None
    calibration: FxCrossSourceCalibration
    error: str | None = None

    def as_fx_quote(self) -> FxQuote:
        return FxQuote(
            symbol=TENCENT_USDCNY,
            current=self.current,
            quote_time=self.quote_time,
            source=self.source,
            error=self.error if self.status == "UNAVAILABLE" else None,
        )


def parse_wscn_daily_fx(
    payload: dict,
    *,
    prod_code: str = WSCN_USDCNY,
) -> list[FxDailyClose]:
    data = payload.get("data") or {}
    fields = data.get("fields") or []
    candle = (data.get("candle") or {}).get(prod_code) or {}
    lines = candle.get("lines") or []
    if "close_px" not in fields or "tick_at" not in fields:
        return []
    close_idx = fields.index("close_px")
    time_idx = fields.index("tick_at")
    result: list[FxDailyClose] = []
    for row in lines:
        if not isinstance(row, list):
            continue
        if max(close_idx, time_idx) >= len(row):
            continue
        try:
            close = Decimal(str(row[close_idx]))
            stamp = int(row[time_idx])
            d = datetime.fromtimestamp(
                stamp,
                tz=timezone.utc,
            ).date()
        except Exception:
            continue
        if close <= 0:
            continue
        result.append(FxDailyClose(date=d, close=close))
    result.sort(key=lambda x: x.date)
    return result


def fetch_wscn_daily_fx(
    *,
    prod_code: str = WSCN_USDCNY,
    count: int = 120,
    timeout: int = 8,
    attempts: int = 3,
) -> list[FxDailyClose]:
    params = {
        "prod_code": prod_code,
        "tick_count": str(count),
        "period_type": "86400",
        "adjust_price_type": "forward",
        "fields": "tick_at,close_px",
    }
    payload = fetch_json_with_retry(
        WSCN_MARKET_KLINE_URL,
        params,
        timeout=timeout,
        attempts=attempts,
        base_delay_seconds=0.25,
    )
    return parse_wscn_daily_fx(payload, prod_code=prod_code)


def _quantile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("EMPTY_VALUES")
    index = min(
        len(ordered) - 1,
        max(0, math.ceil(p * len(ordered)) - 1),
    )
    return ordered[index]


def calibrate_usdcny_sources(
    *,
    tencent_rows: list[FxDailyClose],
    wscn_rows: list[FxDailyClose],
    min_common_count: int = 30,
    max_median_abs_level_diff_bps: float = 10.0,
    max_p90_abs_level_diff_bps: float = 20.0,
) -> FxCrossSourceCalibration:
    tencent = {row.date: row.close for row in tencent_rows}
    wscn = {row.date: row.close for row in wscn_rows}
    common = sorted(set(tencent) & set(wscn))
    if len(common) < min_common_count:
        return FxCrossSourceCalibration(
            status="FAIL",
            common_count=len(common),
            common_start=common[0] if common else None,
            common_end=common[-1] if common else None,
            median_abs_level_diff_bps=None,
            p90_abs_level_diff_bps=None,
            max_abs_level_diff_bps=None,
            error="INSUFFICIENT_CROSS_SOURCE_OVERLAP",
        )

    diffs = [
        abs(float(wscn[d] / tencent[d] - Decimal("1"))) * 10000.0
        for d in common
    ]
    median_abs = statistics.median(diffs)
    p90_abs = _quantile(diffs, 0.90)
    max_abs = max(diffs)
    status = "PASS"
    error = None
    if (
        median_abs > max_median_abs_level_diff_bps
        or p90_abs > max_p90_abs_level_diff_bps
    ):
        status = "FAIL"
        error = "CROSS_SOURCE_BASIS_TOO_WIDE"

    return FxCrossSourceCalibration(
        status=status,
        common_count=len(common),
        common_start=common[0],
        common_end=common[-1],
        median_abs_level_diff_bps=median_abs,
        p90_abs_level_diff_bps=p90_abs,
        max_abs_level_diff_bps=max_abs,
        error=error,
    )


def build_usdcny_cross_source_bridge(
    *,
    nav_date: date,
    tencent_rows: list[FxDailyClose],
    wscn_quote: WscnMarketProxyQuote,
    calibration: FxCrossSourceCalibration,
    as_of: datetime,
    max_quote_age_seconds: int = 180,
) -> FxCrossSourceBridge:
    anchor = fx_close_on(tencent_rows, nav_date)
    if calibration.status != "PASS":
        return FxCrossSourceBridge(
            status="UNAVAILABLE",
            anchor_date=nav_date,
            anchor=anchor,
            current=wscn_quote.current,
            quote_time=wscn_quote.quote_time,
            source="TENCENT_HISTORY_WSCN_CURRENT",
            fx_return=None,
            quote_age_seconds=None,
            calibration=calibration,
            error=calibration.error or "CROSS_SOURCE_CALIBRATION_FAILED",
        )
    if anchor is None or anchor <= 0:
        return FxCrossSourceBridge(
            status="UNAVAILABLE",
            anchor_date=nav_date,
            anchor=None,
            current=wscn_quote.current,
            quote_time=wscn_quote.quote_time,
            source="TENCENT_HISTORY_WSCN_CURRENT",
            fx_return=None,
            quote_age_seconds=None,
            calibration=calibration,
            error="FX_ANCHOR_UNAVAILABLE",
        )
    if (
        wscn_quote.error is not None
        or wscn_quote.current is None
        or wscn_quote.current <= 0
        or wscn_quote.quote_time is None
    ):
        return FxCrossSourceBridge(
            status="UNAVAILABLE",
            anchor_date=nav_date,
            anchor=anchor,
            current=wscn_quote.current,
            quote_time=wscn_quote.quote_time,
            source="TENCENT_HISTORY_WSCN_CURRENT",
            fx_return=None,
            quote_age_seconds=None,
            calibration=calibration,
            error=wscn_quote.error or "WSCN_CURRENT_UNAVAILABLE",
        )

    age = int((as_of - wscn_quote.quote_time).total_seconds())
    if age < -300:
        return FxCrossSourceBridge(
            status="UNAVAILABLE",
            anchor_date=nav_date,
            anchor=anchor,
            current=wscn_quote.current,
            quote_time=wscn_quote.quote_time,
            source="TENCENT_HISTORY_WSCN_CURRENT",
            fx_return=None,
            quote_age_seconds=age,
            calibration=calibration,
            error="FUTURE_FX_QUOTE",
        )

    fx_return = wscn_quote.current / anchor - Decimal("1")
    status = (
        "AVAILABLE"
        if age <= max_quote_age_seconds
        else "STALE"
    )
    return FxCrossSourceBridge(
        status=status,
        anchor_date=nav_date,
        anchor=anchor,
        current=wscn_quote.current,
        quote_time=wscn_quote.quote_time,
        source="TENCENT_HISTORY_WSCN_CURRENT",
        fx_return=fx_return,
        quote_age_seconds=age,
        calibration=calibration,
        error=None if status == "AVAILABLE" else "STALE_WSCN_USDCNY",
    )
