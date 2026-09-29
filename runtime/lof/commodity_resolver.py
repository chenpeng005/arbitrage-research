from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .resolver import EstimatedNavResult


def resolve_r5_commodity_bridge(
    *,
    fund_code: str,
    official_nav: Decimal,
    commodity_anchor: Decimal | None,
    commodity_current: Decimal | None,
    commodity_proxy_id: str,
    fx_anchor: Decimal | None,
    fx_current: Decimal | None,
    as_of: datetime,
    exposure_ratio: Decimal,
    proxy_quality: str,
) -> EstimatedNavResult:
    required = (
        official_nav,
        commodity_anchor,
        commodity_current,
        fx_anchor,
        fx_current,
    )
    if any(x is None or x <= 0 for x in required):
        return EstimatedNavResult(
            fund_code=fund_code,
            estimated_nav=None,
            estimated_nav_time=None,
            estimated_nav_status="UNAVAILABLE",
            estimated_nav_quality="UNKNOWN",
            resolver_class="R5_SPECIAL",
            resolver_method="COMMODITY_FX_BRIDGE",
            proxy_id=commodity_proxy_id,
            proxy_time=None,
            proxy_return=None,
            fx_return=None,
            exposure_ratio_used=exposure_ratio,
            tracking_adjustment_used=None,
            error="MISSING_COMMODITY_BRIDGE_INPUT",
        )

    proxy_return = commodity_current / commodity_anchor - Decimal("1")
    fx_return = fx_current / fx_anchor - Decimal("1")
    estimated_nav = (
        official_nav
        * (Decimal("1") + exposure_ratio * proxy_return)
        * (Decimal("1") + fx_return)
    )

    quality = proxy_quality if proxy_quality in {"HIGH", "MEDIUM", "LOW"} else "UNKNOWN"
    return EstimatedNavResult(
        fund_code=fund_code,
        estimated_nav=estimated_nav,
        estimated_nav_time=as_of,
        estimated_nav_status="AVAILABLE",
        estimated_nav_quality=quality,
        resolver_class="R5_SPECIAL",
        resolver_method="COMMODITY_FX_BRIDGE",
        proxy_id=commodity_proxy_id,
        proxy_time=as_of,
        proxy_return=proxy_return,
        fx_return=fx_return,
        exposure_ratio_used=exposure_ratio,
        tracking_adjustment_used=Decimal("1"),
        error=None,
    )
