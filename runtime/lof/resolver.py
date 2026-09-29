from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal


EstimatedNavStatus = Literal["AVAILABLE", "STALE", "UNAVAILABLE"]
EstimatedNavQuality = Literal["HIGH", "MEDIUM", "LOW", "UNKNOWN"]


@dataclass(frozen=True)
class ResolverInput:
    fund_code: str
    resolver_class: str
    resolver_method: str
    proxy_id: str
    official_nav: Decimal | None
    proxy_anchor_value: Decimal | None
    proxy_current_value: Decimal | None
    proxy_anchor_time: datetime | None
    proxy_current_time: datetime | None
    as_of: datetime
    max_proxy_age_seconds: int
    exposure_ratio: Decimal | None = None
    fx_anchor: Decimal | None = None
    fx_current: Decimal | None = None
    tracking_adjustment: Decimal = Decimal("1")
    quality: EstimatedNavQuality = "UNKNOWN"


@dataclass(frozen=True)
class EstimatedNavResult:
    fund_code: str
    estimated_nav: Decimal | None
    estimated_nav_time: datetime | None
    estimated_nav_status: EstimatedNavStatus
    estimated_nav_quality: EstimatedNavQuality
    resolver_class: str
    resolver_method: str
    proxy_id: str
    proxy_time: datetime | None
    proxy_return: Decimal | None
    fx_return: Decimal | None
    exposure_ratio_used: Decimal | None
    tracking_adjustment_used: Decimal | None
    error: str | None = None


_QUALITY_RANK = {
    "UNKNOWN": 0,
    "LOW": 1,
    "MEDIUM": 2,
    "HIGH": 3,
}


def _downgrade_quality(
    quality: EstimatedNavQuality,
    ceiling: EstimatedNavQuality,
) -> EstimatedNavQuality:
    if _QUALITY_RANK[quality] <= _QUALITY_RANK[ceiling]:
        return quality
    return ceiling


def _seconds_between(later: datetime, earlier: datetime) -> int:
    if later.tzinfo is None and earlier.tzinfo is not None:
        later = later.replace(tzinfo=earlier.tzinfo)
    if earlier.tzinfo is None and later.tzinfo is not None:
        earlier = earlier.replace(tzinfo=later.tzinfo)
    return max(0, int((later - earlier).total_seconds()))


def resolve_estimated_nav(inp: ResolverInput) -> EstimatedNavResult:
    required_values = (
        inp.official_nav,
        inp.proxy_anchor_value,
        inp.proxy_current_value,
    )
    if any(x is None or x <= 0 for x in required_values):
        return EstimatedNavResult(
            fund_code=inp.fund_code,
            estimated_nav=None,
            estimated_nav_time=None,
            estimated_nav_status="UNAVAILABLE",
            estimated_nav_quality="UNKNOWN",
            resolver_class=inp.resolver_class,
            resolver_method=inp.resolver_method,
            proxy_id=inp.proxy_id,
            proxy_time=inp.proxy_current_time,
            proxy_return=None,
            fx_return=None,
            exposure_ratio_used=None,
            tracking_adjustment_used=None,
            error="MISSING_OR_INVALID_INPUT",
        )

    if inp.proxy_anchor_time is None or inp.proxy_current_time is None:
        return EstimatedNavResult(
            fund_code=inp.fund_code,
            estimated_nav=None,
            estimated_nav_time=None,
            estimated_nav_status="UNAVAILABLE",
            estimated_nav_quality="UNKNOWN",
            resolver_class=inp.resolver_class,
            resolver_method=inp.resolver_method,
            proxy_id=inp.proxy_id,
            proxy_time=inp.proxy_current_time,
            proxy_return=None,
            fx_return=None,
            exposure_ratio_used=None,
            tracking_adjustment_used=None,
            error="MISSING_PROXY_TIME",
        )

    if inp.tracking_adjustment <= 0:
        return EstimatedNavResult(
            fund_code=inp.fund_code,
            estimated_nav=None,
            estimated_nav_time=None,
            estimated_nav_status="UNAVAILABLE",
            estimated_nav_quality="UNKNOWN",
            resolver_class=inp.resolver_class,
            resolver_method=inp.resolver_method,
            proxy_id=inp.proxy_id,
            proxy_time=inp.proxy_current_time,
            proxy_return=None,
            fx_return=None,
            exposure_ratio_used=None,
            tracking_adjustment_used=None,
            error="INVALID_TRACKING_ADJUSTMENT",
        )

    age_seconds = _seconds_between(inp.as_of, inp.proxy_current_time)
    status: EstimatedNavStatus = (
        "STALE"
        if age_seconds > inp.max_proxy_age_seconds
        else "AVAILABLE"
    )

    exposure = (
        inp.exposure_ratio
        if inp.exposure_ratio is not None
        else Decimal("1")
    )
    quality = inp.quality
    if inp.exposure_ratio is None:
        quality = _downgrade_quality(quality, "MEDIUM")

    proxy_return = (
        inp.proxy_current_value / inp.proxy_anchor_value - Decimal("1")
    )

    fx_factor = Decimal("1")
    fx_return: Decimal | None = None
    if inp.fx_anchor is not None or inp.fx_current is not None:
        if (
            inp.fx_anchor is None
            or inp.fx_current is None
            or inp.fx_anchor <= 0
            or inp.fx_current <= 0
        ):
            return EstimatedNavResult(
                fund_code=inp.fund_code,
                estimated_nav=None,
                estimated_nav_time=None,
                estimated_nav_status="UNAVAILABLE",
                estimated_nav_quality="UNKNOWN",
                resolver_class=inp.resolver_class,
                resolver_method=inp.resolver_method,
                proxy_id=inp.proxy_id,
                proxy_time=inp.proxy_current_time,
                proxy_return=proxy_return,
                fx_return=None,
                exposure_ratio_used=exposure,
                tracking_adjustment_used=inp.tracking_adjustment,
                error="INCOMPLETE_FX_INPUT",
            )
        fx_factor = inp.fx_current / inp.fx_anchor
        fx_return = fx_factor - Decimal("1")

    estimated_nav = (
        inp.official_nav
        * (Decimal("1") + exposure * proxy_return)
        * fx_factor
        * inp.tracking_adjustment
    )

    if estimated_nav <= 0:
        return EstimatedNavResult(
            fund_code=inp.fund_code,
            estimated_nav=None,
            estimated_nav_time=None,
            estimated_nav_status="UNAVAILABLE",
            estimated_nav_quality="UNKNOWN",
            resolver_class=inp.resolver_class,
            resolver_method=inp.resolver_method,
            proxy_id=inp.proxy_id,
            proxy_time=inp.proxy_current_time,
            proxy_return=proxy_return,
            fx_return=fx_return,
            exposure_ratio_used=exposure,
            tracking_adjustment_used=inp.tracking_adjustment,
            error="NON_POSITIVE_ESTIMATED_NAV",
        )

    return EstimatedNavResult(
        fund_code=inp.fund_code,
        estimated_nav=estimated_nav,
        estimated_nav_time=inp.proxy_current_time,
        estimated_nav_status=status,
        estimated_nav_quality=quality,
        resolver_class=inp.resolver_class,
        resolver_method=inp.resolver_method,
        proxy_id=inp.proxy_id,
        proxy_time=inp.proxy_current_time,
        proxy_return=proxy_return,
        fx_return=fx_return,
        exposure_ratio_used=exposure,
        tracking_adjustment_used=inp.tracking_adjustment,
        error=None,
    )
