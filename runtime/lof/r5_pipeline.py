from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
) -> EstimatedNavResult:
    return EstimatedNavResult(
        fund_code=fund_code,
        estimated_nav=None,
        estimated_nav_time=None,
        estimated_nav_status="UNAVAILABLE",
        estimated_nav_quality="UNKNOWN",
        resolver_class="R5_SPECIAL",
        resolver_method="COMMODITY_FX_BRIDGE",
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
    timeout: int = 12,
) -> EstimatedNavResult:
    if not nav.available or nav.nav is None or nav.nav_date is None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code or "",
            error="MISSING_OFFICIAL_NAV",
        )

    if (
        proxy.status != "RESOLVED"
        or not proxy.commodity_history_symbol
        or not proxy.commodity_live_market
        or not proxy.commodity_live_code
        or proxy.exposure_ratio is None
    ):
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code or "",
            error="UNRESOLVED_COMMODITY_PROXY",
        )

    history = fetch_sina_global_futures_daily(
        proxy.commodity_history_symbol,
        timeout=timeout,
    )
    anchor = commodity_close_on(history, nav.nav_date)

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
        )

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
            )
        fx_current = fx_quote.current
    elif proxy.currency in {None, "CNY"}:
        fx_anchor = Decimal("1")
        fx_current = Decimal("1")
    else:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code,
            error="UNSUPPORTED_COMMODITY_CURRENCY",
        )

    return resolve_r5_commodity_bridge(
        fund_code=nav.code,
        official_nav=nav.nav,
        commodity_anchor=anchor,
        commodity_current=live.current,
        commodity_proxy_id=proxy.commodity_live_code,
        fx_anchor=fx_anchor,
        fx_current=fx_current,
        as_of=as_of,
        exposure_ratio=proxy.exposure_ratio,
        proxy_quality=proxy.proxy_quality,
    )
