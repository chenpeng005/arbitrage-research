from __future__ import annotations

from decimal import Decimal

from .models import LofMarketState


HUNDRED = Decimal("100")


def premium_rate(price: Decimal | None, nav: Decimal | None) -> Decimal | None:
    """Return percentage premium/discount, e.g. 5 means +5%.

    None is returned when either input is unavailable or NAV is non-positive.
    """
    if price is None or nav is None or nav <= 0:
        return None
    return (price / nav - Decimal("1")) * HUNDRED


def premium_projection(state: LofMarketState) -> dict[str, Decimal | str | None]:
    """Project static and estimated premium without conflating their semantics."""
    static_rate = premium_rate(state.price, state.official_nav)
    estimated_rate = premium_rate(state.price, state.estimated_nav)

    return {
        "code": state.code,
        "nav_quality": state.nav_quality,
        "static_premium_rate": static_rate,
        "estimated_premium_rate": estimated_rate,
    }
