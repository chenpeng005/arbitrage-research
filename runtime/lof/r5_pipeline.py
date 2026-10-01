from __future__ import annotations

from datetime import date, datetime

from .commodity_history import (
    commodity_close_on,
    fetch_sina_global_futures_daily,
)
from .commodity_proxy_registry import CommodityProxyEntry
from .commodity_quote import fetch_eastmoney_commodity_quote
from .commodity_resolver import resolve_r5_commodity_bridge
from .fx import fetch_tencent_fx_daily, fetch_tencent_fx_quote, fx_close_on
from .nav import OfficialNavRecord
from .resolver import EstimatedNavResult


def _unavailable(
    *,
    fund_code: str,
    proxy_id: str,
    error: str,
    method: str = "COMMODITY_FX_BRIDGE",
) -> EstimatedNavResult:
    return EstimatedNavResult(
        fund_code=fund_code,
        estimated_nav=None,
        estimated_nav_time=None,
        estimated_nav_status="UNAVAILABLE",
        estimated_nav_quality="UNKNOWN",
        resolver_class="R5_SPECIAL",
        resolver_method=method,
        proxy_id=proxy_id,
        proxy_time=None,
        proxy_return=None,
        fx_return=None,
        exposure_ratio_used=None,
        tracking_adjustment_used=None,
        error=error,
    )


def resolve_r5_commodity_one(
    *,
    nav: OfficialNavRecord,
    proxy: CommodityProxyEntry,
    as_of: datetime,
    expected_anchor_date: date | None = None,
    max_proxy_age_seconds: int = 180,
    timeout: int = 12,
) -> EstimatedNavResult:
    method = (
        "DOMESTIC_FUTURES_PREV_SETTLEMENT"
        if proxy.anchor_mode == "PREVIOUS_SETTLEMENT"
        else "COMMODITY_FX_BRIDGE"
    )

    if not nav.available or nav.nav is None or nav.nav_date is None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code or "",
            error="MISSING_OFFICIAL_NAV",
            method=method,
        )

    if (
        proxy.status != "RESOLVED"
        or not proxy.commodity_live_market
        or not proxy.commodity_live_code
        or proxy.exposure_ratio is None
    ):
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code or "",
            error="UNRESOLVED_COMMODITY_PROXY",
            method=method,
        )

    live = fetch_eastmoney_commodity_quote(
        market=proxy.commodity_live_market,
        code=proxy.commodity_live_code,
        timeout=timeout,
    )
    if live.error is not None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code,
            error=f"COMMODITY_QUOTE_ERROR:{live.error}",
            method=method,
        )

    if proxy.anchor_mode == "PREVIOUS_SETTLEMENT":
        if expected_anchor_date is None or nav.nav_date != expected_anchor_date:
            return _unavailable(
                fund_code=nav.code,
                proxy_id=proxy.commodity_live_code,
                error="NAV_DATE_NOT_PREVIOUS_TRADING_DAY",
                method=method,
            )
        anchor = live.previous_settlement
    else:
        if not proxy.commodity_history_symbol:
            return _unavailable(
                fund_code=nav.code,
                proxy_id=proxy.commodity_live_code,
                error="MISSING_COMMODITY_HISTORY_SYMBOL",
                method=method,
            )
        history = fetch_sina_global_futures_daily(
            proxy.commodity_history_symbol,
            timeout=timeout,
        )
        anchor = commodity_close_on(history, nav.nav_date)

    fx_quote_time = None
    if proxy.currency == "USD":
        fx_rows = fetch_tencent_fx_daily(
            "whUSDCNY",
            count=60,
            timeout=timeout,
        )
        fx_anchor = fx_close_on(fx_rows, nav.nav_date)
        fx_quote = fetch_tencent_fx_quote(
            "whUSDCNY",
            timeout=timeout,
        )
        if fx_quote.error is not None:
            return _unavailable(
                fund_code=nav.code,
                proxy_id=proxy.commodity_live_code,
                error=f"FX_QUOTE_ERROR:{fx_quote.error}",
                method=method,
            )
        fx_current = fx_quote.current
        fx_quote_time = fx_quote.quote_time
    elif proxy.currency in {None, "CNY"}:
        fx_anchor = None
        fx_current = None
    else:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code,
            error="UNSUPPORTED_COMMODITY_CURRENCY",
            method=method,
        )

    return resolve_r5_commodity_bridge(
        fund_code=nav.code,
        official_nav=nav.nav,
        official_nav_date=nav.nav_date,
        commodity_anchor=anchor,
        commodity_current=live.current,
        commodity_proxy_id=proxy.commodity_live_code,
        commodity_quote_time=live.quote_time,
        fx_anchor=fx_anchor,
        fx_current=fx_current,
        fx_quote_time=fx_quote_time,
        as_of=as_of,
        exposure_ratio=proxy.exposure_ratio,
        proxy_quality=proxy.proxy_quality,
        resolver_method=method,
        max_proxy_age_seconds=max_proxy_age_seconds,
    )
