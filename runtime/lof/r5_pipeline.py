from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .commodity_history import (
    commodity_close_on,
    fetch_sina_global_futures_daily,
)
from .commodity_proxy_registry import CommodityProxyEntry
from .commodity_quote import fetch_eastmoney_commodity_quote
from .commodity_resolver import resolve_r5_commodity_bridge
from .fx_resolver import resolve_usdcny_input
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


def _basket_proxy_id(proxy: CommodityProxyEntry) -> str:
    return "+".join(
        f"{component.live_code}:{component.weight}"
        for component in proxy.components
    )


def _resolve_component_basket(
    *,
    proxy: CommodityProxyEntry,
    nav_date: date,
    timeout: int,
) -> tuple[
    Decimal | None,
    Decimal | None,
    datetime | None,
    str,
    str | None,
]:
    if not proxy.components:
        return None, None, None, "", "MISSING_COMMODITY_COMPONENTS"

    total_weight = sum(
        (component.weight for component in proxy.components),
        Decimal("0"),
    )
    if total_weight != Decimal("1"):
        return (
            None,
            None,
            None,
            _basket_proxy_id(proxy),
            f"INVALID_COMMODITY_COMPONENT_WEIGHTS:{total_weight}",
        )

    weighted_return = Decimal("0")
    quote_times: list[datetime] = []
    for component in proxy.components:
        if component.weight <= 0:
            return (
                None,
                None,
                None,
                _basket_proxy_id(proxy),
                f"INVALID_COMMODITY_COMPONENT_WEIGHT:{component.live_code}",
            )

        live = fetch_eastmoney_commodity_quote(
            market=component.live_market,
            code=component.live_code,
            timeout=timeout,
        )
        if live.error is not None or live.current is None:
            return (
                None,
                None,
                None,
                _basket_proxy_id(proxy),
                f"COMMODITY_COMPONENT_QUOTE_ERROR:{component.live_code}:"
                f"{live.error or 'MISSING_CURRENT'}",
            )

        history = fetch_sina_global_futures_daily(
            component.history_symbol,
            timeout=timeout,
        )
        anchor = commodity_close_on(history, nav_date)
        if anchor is None or anchor <= 0:
            return (
                None,
                None,
                None,
                _basket_proxy_id(proxy),
                f"MISSING_COMMODITY_COMPONENT_ANCHOR:{component.live_code}",
            )

        component_return = live.current / anchor - Decimal("1")
        weighted_return += component.weight * component_return
        if live.quote_time is not None:
            quote_times.append(live.quote_time)

    current_factor = Decimal("1") + weighted_return
    if current_factor <= 0:
        return (
            None,
            None,
            None,
            _basket_proxy_id(proxy),
            "NON_POSITIVE_COMMODITY_BASKET_FACTOR",
        )

    return (
        Decimal("1"),
        current_factor,
        min(quote_times) if quote_times else None,
        _basket_proxy_id(proxy),
        None,
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
        "COMMODITY_BASKET_FX_BRIDGE"
        if proxy.components
        else (
            "DOMESTIC_FUTURES_PREV_SETTLEMENT"
            if proxy.anchor_mode == "PREVIOUS_SETTLEMENT"
            else "COMMODITY_FX_BRIDGE"
        )
    )

    if not nav.available or nav.nav is None or nav.nav_date is None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=proxy.commodity_live_code or "",
            error="MISSING_OFFICIAL_NAV",
            method=method,
        )

    if proxy.status != "RESOLVED" or proxy.exposure_ratio is None:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=(
                _basket_proxy_id(proxy)
                if proxy.components
                else (proxy.commodity_live_code or "")
            ),
            error=(proxy.unresolved_reason or "UNRESOLVED_COMMODITY_PROXY"),
            method=method,
        )

    if proxy.components:
        (
            anchor,
            commodity_current,
            commodity_quote_time,
            commodity_proxy_id,
            basket_error,
        ) = _resolve_component_basket(
            proxy=proxy,
            nav_date=nav.nav_date,
            timeout=timeout,
        )
        if basket_error is not None:
            return _unavailable(
                fund_code=nav.code,
                proxy_id=commodity_proxy_id,
                error=basket_error,
                method=method,
            )
    else:
        if not proxy.commodity_live_market or not proxy.commodity_live_code:
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

        commodity_current = live.current
        commodity_quote_time = live.quote_time
        commodity_proxy_id = proxy.commodity_live_code

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
    fx_source = None
    if proxy.currency == "USD":
        fx_input = resolve_usdcny_input(
            nav_date=nav.nav_date,
            as_of=as_of,
            timeout=timeout,
            max_quote_age_seconds=max_proxy_age_seconds,
        )
        fx_anchor = fx_input.anchor
        fx_current = fx_input.current
        fx_quote_time = fx_input.quote_time
        fx_source = fx_input.source
        if (
            fx_anchor is None
            or fx_current is None
            or fx_quote_time is None
            or fx_input.status == "UNAVAILABLE"
        ):
            return _unavailable(
                fund_code=nav.code,
                proxy_id=commodity_proxy_id,
                error=f"FX_INPUT_ERROR:{fx_input.error or 'UNAVAILABLE'}",
                method=method,
            )
        if fx_source == "TENCENT_CNY_ANCHOR_WSCN_CNH_RETURN":
            method = {
                "COMMODITY_FX_BRIDGE": "COMMODITY_CNH_FALLBACK_BRIDGE",
                "COMMODITY_BASKET_FX_BRIDGE": (
                    "COMMODITY_BASKET_CNH_FALLBACK_BRIDGE"
                ),
            }.get(method, method)
    elif proxy.currency in {None, "CNY"}:
        fx_anchor = None
        fx_current = None
        fx_source = None
    else:
        return _unavailable(
            fund_code=nav.code,
            proxy_id=commodity_proxy_id,
            error="UNSUPPORTED_COMMODITY_CURRENCY",
            method=method,
        )

    return resolve_r5_commodity_bridge(
        fund_code=nav.code,
        official_nav=nav.nav,
        official_nav_date=nav.nav_date,
        commodity_anchor=anchor,
        commodity_current=commodity_current,
        commodity_proxy_id=commodity_proxy_id,
        commodity_quote_time=commodity_quote_time,
        fx_anchor=fx_anchor,
        fx_current=fx_current,
        fx_quote_time=fx_quote_time,
        as_of=as_of,
        fx_source=fx_source,
        exposure_ratio=proxy.exposure_ratio,
        proxy_quality=proxy.proxy_quality,
        resolver_method=method,
        max_proxy_age_seconds=max_proxy_age_seconds,
    )
