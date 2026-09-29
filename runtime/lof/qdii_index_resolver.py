from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .resolver import EstimatedNavResult


def resolve_r3_qdii_index_bridge(
    *,
    fund_code: str,
    official_nav: Decimal,
    official_nav_date: date,
    proxy_anchor_close: Decimal | None,
    proxy_latest_close: Decimal | None,
    proxy_latest_date: date | None,
    proxy_id: str,
    fx_anchor: Decimal | None,
    fx_current: Decimal | None,
    as_of: datetime,
    exposure_ratio: Decimal | None,
    proxy_exactness: str,
    timing_quality: str = "MEDIUM",
    intraday_adjustment_return: Decimal | None = None,
) -> EstimatedNavResult:
    """Bridge a stale QDII NAV to current China-market time.

    proxy_exactness:
      EXACT_INDEX -> tracking index itself
      ETF_PROXY   -> high-correlation ETF proxy
      OTHER_PROXY -> lower-confidence proxy
    """
    required = (
        official_nav,
        proxy_anchor_close,
        proxy_latest_close,
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
            resolver_class="R3_QDII_INDEX",
            resolver_method="MULTIDAY_PROXY_FX_BRIDGE",
            proxy_id=proxy_id,
            proxy_time=None,
            proxy_return=None,
            fx_return=None,
            exposure_ratio_used=exposure_ratio,
            tracking_adjustment_used=None,
            error="MISSING_BRIDGE_INPUT",
        )

    exposure = exposure_ratio if exposure_ratio is not None else Decimal("1")
    proxy_return = proxy_latest_close / proxy_anchor_close - Decimal("1")
    if intraday_adjustment_return is not None:
        proxy_factor = (
            (Decimal("1") + proxy_return)
            * (Decimal("1") + intraday_adjustment_return)
        )
        proxy_return = proxy_factor - Decimal("1")

    fx_return = fx_current / fx_anchor - Decimal("1")

    estimated_nav = (
        official_nav
        * (Decimal("1") + exposure * proxy_return)
        * (Decimal("1") + fx_return)
    )

    quality_rank = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
    if proxy_exactness == "EXACT_INDEX" and exposure_ratio is not None:
        proxy_quality = "HIGH"
    elif proxy_exactness in {"ETF_PROXY", "ETF_SAME_INDEX"}:
        proxy_quality = "MEDIUM"
    else:
        proxy_quality = "LOW"

    quality = (
        proxy_quality
        if quality_rank[proxy_quality] <= quality_rank.get(timing_quality, 0)
        else timing_quality
    )

    # The proxy may be the latest completed overseas session rather than a
    # currently-trading instrument. timing_quality captures this cross-market
    # alignment. U.S. last-close-only estimates during China hours should
    # normally be MEDIUM until a futures / live overlay is applied.
    return EstimatedNavResult(
        fund_code=fund_code,
        estimated_nav=estimated_nav,
        estimated_nav_time=as_of,
        estimated_nav_status="AVAILABLE",
        estimated_nav_quality=quality,
        resolver_class="R3_QDII_INDEX",
        resolver_method="MULTIDAY_PROXY_FX_BRIDGE",
        proxy_id=proxy_id,
        proxy_time=None,
        proxy_return=proxy_return,
        fx_return=fx_return,
        exposure_ratio_used=exposure,
        tracking_adjustment_used=Decimal("1"),
        error=None,
    )
