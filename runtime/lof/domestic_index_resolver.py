from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from .index_quote import IndexQuote
from .resolver import EstimatedNavResult, ResolverInput, resolve_estimated_nav


def resolve_r1_from_previous_close(
    *,
    fund_code: str,
    official_nav: Decimal,
    official_nav_date: date,
    expected_anchor_date: date,
    index_quote: IndexQuote,
    as_of: datetime,
    exposure_ratio: Decimal | None,
    max_proxy_age_seconds: int = 60,
) -> EstimatedNavResult:
    """R1 fast path when official NAV is anchored to prior index close.

    Caller must provide expected_anchor_date from the trading-calendar layer.
    We fail closed when official NAV date does not equal that date.
    """
    if official_nav_date != expected_anchor_date:
        return EstimatedNavResult(
            fund_code=fund_code,
            estimated_nav=None,
            estimated_nav_time=None,
            estimated_nav_status="UNAVAILABLE",
            estimated_nav_quality="UNKNOWN",
            resolver_class="R1_DOMESTIC_INDEX",
            resolver_method="INDEX_PROXY_PREV_CLOSE",
            proxy_id=index_quote.code or index_quote.symbol,
            proxy_time=index_quote.quote_time,
            proxy_return=None,
            fx_return=None,
            exposure_ratio_used=exposure_ratio,
            tracking_adjustment_used=None,
            error="NAV_DATE_NOT_PREVIOUS_TRADING_DAY",
        )

    if index_quote.error is not None:
        return EstimatedNavResult(
            fund_code=fund_code,
            estimated_nav=None,
            estimated_nav_time=None,
            estimated_nav_status="UNAVAILABLE",
            estimated_nav_quality="UNKNOWN",
            resolver_class="R1_DOMESTIC_INDEX",
            resolver_method="INDEX_PROXY_PREV_CLOSE",
            proxy_id=index_quote.code or index_quote.symbol,
            proxy_time=index_quote.quote_time,
            proxy_return=None,
            fx_return=None,
            exposure_ratio_used=exposure_ratio,
            tracking_adjustment_used=None,
            error=f"INDEX_QUOTE_ERROR:{index_quote.error}",
        )

    quality = "HIGH" if exposure_ratio is not None else "MEDIUM"
    tz = index_quote.quote_time.tzinfo if index_quote.quote_time is not None else as_of.tzinfo
    proxy_anchor_time = datetime.combine(
        expected_anchor_date,
        time(15, 0),
        tzinfo=tz,
    )

    return resolve_estimated_nav(
        ResolverInput(
            fund_code=fund_code,
            resolver_class="R1_DOMESTIC_INDEX",
            resolver_method="INDEX_PROXY_PREV_CLOSE",
            proxy_id=index_quote.code or index_quote.symbol,
            official_nav=official_nav,
            proxy_anchor_value=index_quote.previous_close,
            proxy_current_value=index_quote.current,
            proxy_anchor_time=proxy_anchor_time,
            proxy_current_time=index_quote.quote_time,
            as_of=as_of,
            max_proxy_age_seconds=max_proxy_age_seconds,
            exposure_ratio=exposure_ratio,
            quality=quality,
        )
    )
