from __future__ import annotations

from dataclasses import asdict, dataclass, field
from math import ceil, floor
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
    nav_date: str | None = None

    # Explicit normalized capacity state. Parsers set UNLIMITED only when the
    # official PCF actually contains the relevant cap fields with no positive
    # cap. Missing evidence remains UNKNOWN.
    market_capacity_status: str | None = None
    account_capacity_status: str | None = None

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

    @staticmethod
    def _positive(value: int | None) -> int | None:
        return value if value is not None and value > 0 else None

    @staticmethod
    def _binding_kind(cumulative: int | None, net: int | None) -> str | None:
        cumulative = PcfSnapshot._positive(cumulative)
        net = PcfSnapshot._positive(net)
        if cumulative is not None and net is not None:
            if cumulative == net:
                return "BOTH"
            return "CUMULATIVE" if cumulative < net else "NET"
        if cumulative is not None:
            return "CUMULATIVE"
        if net is not None:
            return "NET"
        return None

    @staticmethod
    def _binding_limit(cumulative: int | None, net: int | None) -> int | None:
        limits = [
            value
            for value in (
                PcfSnapshot._positive(cumulative),
                PcfSnapshot._positive(net),
            )
            if value is not None
        ]
        return min(limits) if limits else None


    @staticmethod
    def _resolved_capacity_status(
        explicit_status: str | None,
        *,
        creation_allowed: bool | None,
        cumulative: int | None,
        net: int | None,
    ) -> str:
        if creation_allowed is False:
            return "CLOSED"
        if creation_allowed is not True:
            return "UNKNOWN"
        if PcfSnapshot._binding_limit(cumulative, net) is not None:
            return "LIMITED"
        if explicit_status == "UNLIMITED":
            return "UNLIMITED"
        return "UNKNOWN"

    def resolved_market_capacity_status(self) -> str:
        return self._resolved_capacity_status(
            self.market_capacity_status,
            creation_allowed=self.creation_allowed,
            cumulative=self.creation_limit,
            net=self.net_creation_limit,
        )

    def resolved_account_capacity_status(self) -> str:
        return self._resolved_capacity_status(
            self.account_capacity_status,
            creation_allowed=self.creation_allowed,
            cumulative=self.account_creation_limit,
            net=self.account_net_creation_limit,
        )

    def market_capacity_kind(self) -> str | None:
        """Which market-wide rule binds a creation with no offsetting redemption."""
        return self._binding_kind(self.creation_limit, self.net_creation_limit)

    def account_capacity_kind(self) -> str | None:
        """Which account-level rule binds a creation with no offsetting redemption."""
        return self._binding_kind(
            self.account_creation_limit,
            self.account_net_creation_limit,
        )

    def market_creation_limit(self) -> int | None:
        """Binding market-wide creation limit before same-day redemption offsets.

        Cumulative and net caps are distinct and are never added. When both are
        present, a standalone creation must satisfy both, so the smaller positive
        value is the binding no-offset limit. A NET binding limit is headroom,
        not a hard ceiling on gross creations after same-day redemptions.
        """
        return self._binding_limit(self.creation_limit, self.net_creation_limit)

    def account_creation_cap(self) -> int | None:
        """Binding account-level creation limit before same-day redemption offsets."""
        return self._binding_limit(
            self.account_creation_limit,
            self.account_net_creation_limit,
        )

    def market_limit_basket_equivalent(self) -> float | None:
        limit = self.market_creation_limit()
        unit = self.creation_redemption_unit
        if limit is None or unit is None or unit <= 0:
            return None
        return limit / unit

    def account_limit_basket_equivalent(self) -> float | None:
        limit = self.account_creation_cap()
        unit = self.creation_redemption_unit
        if limit is None or unit is None or unit <= 0:
            return None
        return limit / unit

    def total_baskets(self) -> int | None:
        """Whole creation units fitting inside the binding no-offset market limit."""
        equivalent = self.market_limit_basket_equivalent()
        if equivalent is None:
            return None
        return floor(equivalent)

    def account_baskets(self) -> int | None:
        """Whole creation units fitting inside the binding no-offset account limit."""
        equivalent = self.account_limit_basket_equivalent()
        if equivalent is None:
            return None
        return floor(equivalent)

    def minimum_accounts_to_fill(self) -> int | None:
        """Lower-bound account count for the no-offset executable basket capacity."""
        total = self.total_baskets()
        per_account = self.account_baskets()
        if (
            total is None
            or per_account is None
            or total <= 0
            or per_account <= 0
        ):
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
        market_kind = self.market_capacity_kind()
        payload.update(
            {
                "market_creation_limit": self.market_creation_limit(),
                "account_creation_cap": self.account_creation_cap(),
                "capacity_kind": market_kind,
                "market_capacity_kind": market_kind,
                "account_capacity_kind": self.account_capacity_kind(),
                "market_capacity_status": self.resolved_market_capacity_status(),
                "account_capacity_status": self.resolved_account_capacity_status(),
                "market_limit_basket_equivalent": self.market_limit_basket_equivalent(),
                "account_limit_basket_equivalent": self.account_limit_basket_equivalent(),
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
    previous_value: float | int | str | None = None
    current_value: float | int | str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
