from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .india_index_quote import DelayedGlobalIndexQuote


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class IndiaFxBridgeResult:
    status: str
    quality: str
    fx_return: Decimal | None
    current_inr_cny: Decimal | None
    previous_inr_cny: Decimal | None
    usd_inr_time: datetime | None
    usd_cny_time: datetime | None
    max_quote_age_seconds: int | None
    error: str | None = None


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def resolve_inr_cny_bridge(
    *,
    usd_inr: DelayedGlobalIndexQuote,
    usd_cny: DelayedGlobalIndexQuote,
    as_of: datetime,
    max_quote_age_seconds: int = 1800,
) -> IndiaFxBridgeResult:
    def result(
        *,
        status: str,
        quality: str,
        error: str | None,
        fx_return: Decimal | None = None,
        current_inr_cny: Decimal | None = None,
        previous_inr_cny: Decimal | None = None,
        max_age: int | None = None,
    ) -> IndiaFxBridgeResult:
        return IndiaFxBridgeResult(
            status=status,
            quality=quality,
            fx_return=fx_return,
            current_inr_cny=current_inr_cny,
            previous_inr_cny=previous_inr_cny,
            usd_inr_time=usd_inr.quote_time,
            usd_cny_time=usd_cny.quote_time,
            max_quote_age_seconds=max_age,
            error=error,
        )

    if usd_inr.error:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error=f"USDINR:{usd_inr.error}",
        )
    if usd_cny.error:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error=f"USDCNY:{usd_cny.error}",
        )

    values = (
        usd_inr.current,
        usd_inr.previous_close,
        usd_cny.current,
        usd_cny.previous_close,
    )
    if any(value is None or value <= 0 for value in values):
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="INVALID_FX_INPUT",
        )
    if usd_inr.quote_time is None or usd_cny.quote_time is None:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="MISSING_FX_TIMESTAMP",
        )

    local_as_of = _local(as_of)
    ages = [
        int((local_as_of - _local(usd_inr.quote_time)).total_seconds()),
        int((local_as_of - _local(usd_cny.quote_time)).total_seconds()),
    ]
    if min(ages) < -300:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="FUTURE_FX_QUOTE",
            max_age=max(ages),
        )
    max_age = max(ages)
    if max_age > max_quote_age_seconds:
        return result(
            status="STALE",
            quality="LOW",
            error="STALE_FX_QUOTE",
            max_age=max_age,
        )

    current_cross = usd_cny.current / usd_inr.current
    previous_cross = usd_cny.previous_close / usd_inr.previous_close
    fx_return = current_cross / previous_cross - Decimal("1")
    return result(
        status="AVAILABLE",
        quality="LOW",
        error=None,
        fx_return=fx_return,
        current_inr_cny=current_cross,
        previous_inr_cny=previous_cross,
        max_age=max_age,
    )
