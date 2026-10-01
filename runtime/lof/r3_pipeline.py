from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

from .foreign_quote import fetch_tencent_foreign_quote
from .futures_overlay import fetch_eastmoney_global_futures
from .fx import (
    fetch_tencent_fx_daily,
    fetch_tencent_fx_quote,
    fx_close_on,
)
from .hk_history import fetch_tencent_hk_daily, hk_close_on
from .mapping import ResolverMappingCandidate
from .nav import OfficialNavRecord
from .qdii_index_resolver import resolve_r3_qdii_index_bridge
from .qdii_proxy_registry import QdiiProxyEntry
from .resolver import EstimatedNavResult
from .us_history import close_on, fetch_tencent_us_daily, latest_close


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
        resolver_class="R3_QDII_INDEX",
        resolver_method="MULTIDAY_PROXY_FX_BRIDGE",
        proxy_id=proxy_id,
        proxy_time=None,
        proxy_return=None,
        fx_return=None,
        exposure_ratio_used=None,
        tracking_adjustment_used=None,
        error=error,
    )


def resolve_r3_one(
    *,
    nav: OfficialNavRecord,
    mapping: ResolverMappingCandidate,
    proxy: QdiiProxyEntry,
    as_of: datetime,
    timeout: int = 10,
) -> EstimatedNavResult:
    if not nav.available or nav.nav is None or nav.nav_date is None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.proxy_symbol or "",
            error="MISSING_OFFICIAL_NAV",
        )
    if proxy.proxy_type == "UNRESOLVED" or not proxy.proxy_symbol:
        return _unavailable(
            fund_code=nav.code,
            proxy_id="",
            error="UNRESOLVED_PROXY",
        )

    exposure = (
        mapping.exposure_ratio_candidate
        if mapping.exposure_ratio_candidate is not None
        else proxy.exposure_ratio
    )

    fx_symbol = {
        "USD": "whUSDCNY",
        "HKD": "whHKDCNY",
    }.get(proxy.currency)
    if fx_symbol is None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.proxy_symbol,
            error="UNSUPPORTED_CURRENCY",
        )

    fx_rows = fetch_tencent_fx_daily(
        fx_symbol,
        count=40,
        timeout=timeout,
    )
    fx_anchor = fx_close_on(fx_rows, nav.nav_date)
    fx_quote = fetch_tencent_fx_quote(
        fx_symbol,
        timeout=timeout,
    )
    if fx_quote.error is not None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.proxy_symbol,
            error=f"FX_QUOTE_ERROR:{fx_quote.error}",
        )

    if proxy.proxy_symbol.startswith("hk"):
        history = fetch_tencent_hk_daily(
            symbol=proxy.history_symbol or proxy.proxy_symbol,
            count=40,
            timeout=timeout,
        )
        anchor = hk_close_on(history, nav.nav_date)
        live_quote = fetch_tencent_foreign_quote(
            proxy.proxy_symbol,
            timeout=timeout,
        )
        if live_quote.error is not None:
            return _unavailable(
                fund_code=nav.code,
                proxy_id=proxy.proxy_symbol,
                error=f"PROXY_QUOTE_ERROR:{live_quote.error}",
            )
        current = live_quote.current
        latest_date = as_of.date()
        proxy_time = live_quote.quote_time
        timing_quality = "HIGH"
        enforce_realtime_freshness = True
        resolver_method = "HK_LIVE_INDEX_FX_BRIDGE"
        exactness = (
            "EXACT_INDEX"
            if proxy.proxy_type == "DIRECT_INDEX"
            else "ETF_PROXY"
        )
    elif proxy.proxy_symbol.startswith("us"):
        history_symbol = proxy.history_symbol or proxy.proxy_symbol
        history = fetch_tencent_us_daily(
            symbol_with_exchange=history_symbol,
            count=40,
            timeout=timeout,
        )
        anchor = close_on(history, nav.nav_date)
        latest = latest_close(history)
        current = latest.close if latest else None
        latest_date = latest.date if latest else None
        # During China trading hours the U.S. cash market is closed. We may
        # overlay current index futures to bridge from the latest cash close.
        timing_quality = "MEDIUM"
        proxy_time = None
        enforce_realtime_freshness = False
        resolver_method = "MULTIDAY_PROXY_FX_BRIDGE"
        exactness = (
            "EXACT_INDEX"
            if proxy.proxy_type == "DIRECT_INDEX"
            else "ETF_PROXY"
        )
    else:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.proxy_symbol,
            error="UNSUPPORTED_PROXY_MARKET",
        )

    intraday_adjustment_return = None
    if (
        proxy.futures_overlay_market
        and proxy.futures_overlay_code
        and proxy.proxy_symbol.startswith("us")
    ):
        try:
            futures_quote = fetch_eastmoney_global_futures(
                market=proxy.futures_overlay_market,
                code=proxy.futures_overlay_code,
                timeout=timeout,
            )
            if futures_quote.error is None:
                intraday_adjustment_return = futures_quote.adjustment_return
                if proxy.futures_overlay_quality:
                    timing_quality = proxy.futures_overlay_quality
        except Exception:
            # Fail open to the latest completed cash close. The estimate remains
            # available but keeps its lower timing quality.
            intraday_adjustment_return = None

    return resolve_r3_qdii_index_bridge(
        fund_code=nav.code,
        official_nav=nav.nav,
        official_nav_date=nav.nav_date,
        proxy_anchor_close=anchor,
        proxy_latest_close=current,
        proxy_latest_date=latest_date,
        proxy_id=proxy.proxy_symbol,
        fx_anchor=fx_anchor,
        fx_current=fx_quote.current,
        as_of=as_of,
        exposure_ratio=exposure,
        proxy_exactness=exactness,
        timing_quality=timing_quality,
        intraday_adjustment_return=intraday_adjustment_return,
        proxy_time=proxy_time,
        fx_time=fx_quote.quote_time,
        max_proxy_age_seconds=180,
        enforce_realtime_freshness=enforce_realtime_freshness,
        resolver_method=resolver_method,
    )
