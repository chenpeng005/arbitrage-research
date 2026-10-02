from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from .wscn_market_proxy import WscnMarketProxyQuote


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
BOND_EXPOSURE = Decimal("0.90")
DURATION_YEARS = Decimal("5.0")


@dataclass(frozen=True)
class UsdBondShadowResult:
    fund_code: str
    estimated_nav: Decimal | None
    shadow_status: str
    shadow_quality: str
    resolver_method: str
    yield_change_pp: Decimal | None
    bond_return: Decimal | None
    fx_return: Decimal | None
    us10y_time: datetime | None
    usdcny_time: datetime | None
    max_input_age_seconds: int | None
    eligible_for_main: bool
    error: str | None = None


def _local(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def resolve_501300_usd_bond_shadow(
    *,
    official_nav: Decimal,
    official_nav_lag_label: str | None,
    us10y: WscnMarketProxyQuote,
    usdcny: WscnMarketProxyQuote,
    as_of: datetime,
    max_input_age_seconds: int = 1800,
) -> UsdBondShadowResult:
    def result(
        *,
        status: str,
        quality: str,
        error: str | None,
        estimated_nav: Decimal | None = None,
        yield_change_pp: Decimal | None = None,
        bond_return: Decimal | None = None,
        fx_return: Decimal | None = None,
        max_age: int | None = None,
    ) -> UsdBondShadowResult:
        return UsdBondShadowResult(
            fund_code="501300",
            estimated_nav=estimated_nav,
            shadow_status=status,
            shadow_quality=quality,
            resolver_method="R4_USD_BOND_US10Y_FX_SHADOW_V0",
            yield_change_pp=yield_change_pp,
            bond_return=bond_return,
            fx_return=fx_return,
            us10y_time=us10y.quote_time,
            usdcny_time=usdcny.quote_time,
            max_input_age_seconds=max_age,
            eligible_for_main=False,
            error=error,
        )

    if official_nav <= 0:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="INVALID_OFFICIAL_NAV",
        )

    if official_nav_lag_label != "T-1":
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error=f"OFFICIAL_NAV_NOT_T1:{official_nav_lag_label or 'UNKNOWN'}",
        )

    for label, quote in (("US10Y", us10y), ("USDCNY", usdcny)):
        if quote.error:
            return result(
                status="UNAVAILABLE",
                quality="UNKNOWN",
                error=f"{label}:{quote.error}",
            )
        if (
            quote.current is None
            or quote.previous_close is None
            or quote.previous_close <= 0
            or quote.quote_time is None
        ):
            return result(
                status="UNAVAILABLE",
                quality="UNKNOWN",
                error=f"{label}:INVALID_INPUT",
            )

    local_as_of = _local(as_of)
    ages = [
        int((local_as_of - _local(us10y.quote_time)).total_seconds()),
        int((local_as_of - _local(usdcny.quote_time)).total_seconds()),
    ]
    if min(ages) < -300:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="FUTURE_INPUT_QUOTE",
            max_age=max(ages),
        )

    max_age = max(ages)
    if max_age > max_input_age_seconds:
        return result(
            status="STALE",
            quality="LOW",
            error="STALE_INPUT_QUOTE",
            max_age=max_age,
        )

    yield_change_pp = us10y.current - us10y.previous_close
    bond_return = (
        BOND_EXPOSURE
        * (-DURATION_YEARS)
        * yield_change_pp
        / Decimal("100")
    )
    fx_return = usdcny.current / usdcny.previous_close - Decimal("1")
    estimated_nav = (
        official_nav
        * (Decimal("1") + bond_return)
        * (Decimal("1") + fx_return)
    )

    if estimated_nav <= 0:
        return result(
            status="UNAVAILABLE",
            quality="UNKNOWN",
            error="NON_POSITIVE_ESTIMATED_NAV",
            yield_change_pp=yield_change_pp,
            bond_return=bond_return,
            fx_return=fx_return,
            max_age=max_age,
        )

    return result(
        status="AVAILABLE",
        quality="LOW",
        error=None,
        estimated_nav=estimated_nav,
        yield_change_pp=yield_change_pp,
        bond_return=bond_return,
        fx_return=fx_return,
        max_age=max_age,
    )
