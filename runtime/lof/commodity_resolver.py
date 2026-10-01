from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from .resolver import EstimatedNavResult, ResolverInput, resolve_estimated_nav


def resolve_r5_commodity_bridge(
    *,
    fund_code: str,
    official_nav: Decimal,
    official_nav_date: date,
    commodity_anchor: Decimal | None,
    commodity_current: Decimal | None,
    commodity_proxy_id: str,
    commodity_quote_time: datetime | None,
    fx_anchor: Decimal | None,
    fx_current: Decimal | None,
    fx_quote_time: datetime | None,
    as_of: datetime,
    exposure_ratio: Decimal,
    proxy_quality: str,
    resolver_method: str = "COMMODITY_FX_BRIDGE",
    max_proxy_age_seconds: int = 180,
) -> EstimatedNavResult:
    times = [x for x in (commodity_quote_time, fx_quote_time) if x is not None]
    effective_time = min(times) if times else None
    tz = effective_time.tzinfo if effective_time is not None else as_of.tzinfo
    anchor_time = datetime.combine(
        official_nav_date,
        time.min,
        tzinfo=tz,
    )

    return resolve_estimated_nav(
        ResolverInput(
            fund_code=fund_code,
            resolver_class="R5_SPECIAL",
            resolver_method=resolver_method,
            proxy_id=commodity_proxy_id,
            official_nav=official_nav,
            proxy_anchor_value=commodity_anchor,
            proxy_current_value=commodity_current,
            proxy_anchor_time=anchor_time,
            proxy_current_time=effective_time,
            as_of=as_of,
            max_proxy_age_seconds=max_proxy_age_seconds,
            exposure_ratio=exposure_ratio,
            fx_anchor=fx_anchor,
            fx_current=fx_current,
            tracking_adjustment=Decimal("1"),
            quality=(
                proxy_quality
                if proxy_quality in {"HIGH", "MEDIUM", "LOW"}
                else "UNKNOWN"
            ),
        )
    )
