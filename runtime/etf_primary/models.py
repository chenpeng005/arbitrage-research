from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil
from typing import Any


@dataclass(frozen=True)
class EtfIdentity:
    code: str
    name: str
    exchange: str
    manager: str | None = None
    tracking_index: str | None = None
    listing_date: str | None = None

    # Orthogonal classification dimensions. UI may group them as a tree,
    # but storage does not force one taxonomy.
    region_scope: str = "UNKNOWN"
    asset_class: str = "OTHER"
    strategy_style: str = "UNKNOWN"
    qdii_flag: bool | None = None

    raw_exchange_class: str | None = None
    classification_reason: str | None = None

    universe_source_url: str | None = None
    pcf_page_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PcfSnapshot:
    code: str
    exchange: str
    trade_date: str | None

    creation_allowed: bool | None = None
    redemption_allowed: bool | None = None
    creation_redemption_unit: int | None = None

    nav_per_cu: float | None = None
    nav_per_share: float | None = None

    creation_limit: int | None = None
    redemption_limit: int | None = None
    net_creation_limit: int | None = None
    net_redemption_limit: int | None = None

    account_creation_limit: int | None = None
    account_redemption_limit: int | None = None
    account_net_creation_limit: int | None = None
    account_net_redemption_limit: int | None = None

    creation_redemption_mode: str | None = None
    source_url: str | None = None
    fetched_at: str | None = None
    raw_header: dict[str, Any] = field(default_factory=dict)

    def market_creation_limit(self) -> int | None:
        """Best hard market-wide creation cap available for the day.

        Cumulative and net caps are different concepts. We never add them.
        Cumulative cap is preferred because it is a true gross ceiling;
        otherwise the net cap is exposed as the best available constraint.
        """
        if self.creation_limit is not None and self.creation_limit > 0:
            return self.creation_limit
        if self.net_creation_limit is not None and self.net_creation_limit > 0:
            return self.net_creation_limit
        return None

    def account_creation_cap(self) -> int | None:
        """Best account-level creation cap available for the day."""
        if self.account_creation_limit is not None and self.account_creation_limit > 0:
            return self.account_creation_limit
        if self.account_net_creation_limit is not None and self.account_net_creation_limit > 0:
            return self.account_net_creation_limit
        return None

    def total_baskets(self) -> float | None:
        limit = self.market_creation_limit()
        unit = self.creation_redemption_unit
        if limit is None or unit is None or unit <= 0:
            return None
        return limit / unit

    def account_baskets(self) -> float | None:
        limit = self.account_creation_cap()
        unit = self.creation_redemption_unit
        if limit is None or unit is None or unit <= 0:
            return None
        return limit / unit

    def minimum_accounts_to_fill(self) -> int | None:
        total = self.total_baskets()
        per_account = self.account_baskets()
        if total is None or per_account is None or per_account <= 0:
            return None
        return ceil(total / per_account)

    def basket_value(self) -> float | None:
        """Approximate capital represented by one creation unit."""
        if self.nav_per_cu is not None and self.nav_per_cu > 0:
            return self.nav_per_cu
        if (
            self.nav_per_share is not None
            and self.creation_redemption_unit is not None
            and self.creation_redemption_unit > 0
        ):
            return self.nav_per_share * self.creation_redemption_unit
        return None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "market_creation_limit": self.market_creation_limit(),
                "account_creation_cap": self.account_creation_cap(),
                "total_baskets": self.total_baskets(),
                "account_baskets": self.account_baskets(),
                "minimum_accounts_to_fill": self.minimum_accounts_to_fill(),
                "basket_value": self.basket_value(),
            }
        )
        return payload


@dataclass(frozen=True)
class MonitorEvent:
    code: str
    exchange: str
    event_type: str
    severity: str
    message: str
    previous_value: float | int | None = None
    current_value: float | int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
