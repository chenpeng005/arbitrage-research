from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from .india_index_quote import DelayedGlobalIndexQuote


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
INDIA_CASH_OPEN_CST = time(11, 45)
INDIA_CASH_CLOSE_CST = time(18, 0)


@dataclass(frozen=True)
class IndiaTimingShadowResult:
    fund_code: str
    estimated_nav: Decimal | None
    shadow_status: str
    shadow_quality: str
    resolver_method: str
    proxy_id: str
    proxy_time: datetime | None
    proxy_return: Decimal | None
    quote_age_seconds: int | None
    timing_regime: str
    eligible_for_main: bool
    error: str | None = None


def _shanghai_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def india_cash_timing_regime(as_of: datetime) -> str:
    local = _shanghai_time(as_of)
    if local.weekday() >= 5:
        return "INDIA_CASH_WEEKEND"
    clock = local.time().replace(tzinfo=None)
    if clock < INDIA_CASH_OPEN_CST:
        return "INDIA_CASH_NOT_OPEN"
    if clock <= INDIA_CASH_CLOSE_CST:
        return "INDIA_CASH_OPEN"
    return "INDIA_CASH_CLOSED"


def resolve_164824_india_shadow(
    *,
    official_nav: Decimal,
    quote: DelayedGlobalIndexQuote,
    as_of: datetime,
    max_quote_age_seconds: int = 1800,
) -> IndiaTimingShadowResult:
    fund_code = "164824"
    proxy_id = quote.secid or "100.SENSEX"
    regime = india_cash_timing_regime(as_of)

    def result(
        *,
        status: str,
        quality: str,
        error: str | None,
        estimated_nav: Decimal | None = None,
        proxy_return: Decimal | None = None,
        quote_age_seconds: int | None = None,
    ) -> IndiaTimingShadowResult:
        return IndiaTimingShadowResult(
            fund_code=fund_code,
            estimated_nav=estimated_nav,
            shadow_status=status,
            shadow_quality=quality,
            resolver_method="R4_INDIA_SENSEX_TIMING_SHADOW_V0",
            proxy_id=proxy_id,
            proxy_time=quote.quote_time,
            proxy_return=proxy_return,
            quote_age_seconds=quote_age_seconds,
            timing_regime=regime,
            eligible_for_main=False,
            error=error,
        )

    if official_nav <= 0:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="INVALID_OFFICIAL_NAV",
        )

    if regime != "INDIA_CASH_OPEN":
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error=regime,
        )

    proxy_return = quote.session_return
    if quote.error or quote.quote_time is None or proxy_return is None:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error=quote.error or "MISSING_SENSEX_QUOTE",
        )

    local_as_of = _shanghai_time(as_of)
    local_quote_time = _shanghai_time(quote.quote_time)
    quote_age_seconds = int((local_as_of - local_quote_time).total_seconds())
    if quote_age_seconds < -300:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="FUTURE_SENSEX_QUOTE",
            proxy_return=proxy_return,
            quote_age_seconds=quote_age_seconds,
        )
    if quote_age_seconds > max_quote_age_seconds:
        return result(
            status="STALE",
            quality="LOW",
            error="STALE_SENSEX_QUOTE",
            proxy_return=proxy_return,
            quote_age_seconds=quote_age_seconds,
        )

    estimated_nav = official_nav * (Decimal("1") + proxy_return)
    return result(
        status="AVAILABLE",
        quality="LOW",
        error=None,
        estimated_nav=estimated_nav,
        proxy_return=proxy_return,
        quote_age_seconds=quote_age_seconds,
    )
