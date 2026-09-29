from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal


NavQuality = Literal[
    "OFFICIAL_CURRENT",
    "OFFICIAL_STALE",
    "ESTIMATED",
    "UNAVAILABLE",
]


@dataclass(frozen=True)
class LofMarketState:
    code: str
    name: str
    exchange: str
    price: Decimal | None
    price_time: datetime | None
    amount: Decimal | None = None
    official_nav: Decimal | None = None
    official_nav_date: date | None = None
    estimated_nav: Decimal | None = None
    estimated_nav_time: datetime | None = None
    nav_quality: NavQuality = "UNAVAILABLE"


@dataclass(frozen=True)
class LofSubscriptionState:
    subscription_status: str
    redemption_status: str
    daily_subscription_limit: Decimal | None = None
    limit_scope: str | None = None
    subscription_to_sell_days: int | None = None
    subscription_fee_rate: Decimal = Decimal("0")
    selling_fee_rate: Decimal = Decimal("0")
    other_explicit_cost: Decimal = Decimal("0")
