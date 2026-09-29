from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from .classification import FundTypeRecord, classify_universe, fetch_fund_type_map
from .context_builder import (
    EstimatedNavContextBuildResult,
    build_estimated_nav_context,
)
from .controller import collect_market_snapshot
from .estimated_nav_lane import EstimatedNavContext
from .nav import OfficialNavRecord
from .snapshot_store import LofSnapshotStore
from .state import FundTradeStateRecord
from .szse_relay import (
    DEFAULT_RELAY_MAX_AGE_SECONDS,
    DEFAULT_SZSE_RELAY_BASE_URL,
    SzseRelayBundle,
    fetch_szse_relay_bundle,
    validate_szse_relay_freshness,
)
from .trading_calendar import fetch_previous_trading_day
from .tracking_index import load_tracking_index_fixture
from .universe import LofIdentity, fetch_all_lof_universe


@dataclass
class LofRuntimeSession:
    """Holds low-frequency LOF metadata for repeated high-frequency snapshots."""

    universe: list[LofIdentity]
    type_records: dict[tuple[str, str], FundTypeRecord]
    estimated_nav_context: EstimatedNavContext
    context_build: EstimatedNavContextBuildResult
    context_built_at: datetime
    second_previous_trading_day: date | None = None
    szse_transport: str = "DIRECT_OFFICIAL"
    szse_relay_bundle: SzseRelayBundle | None = None

    @classmethod
    def build(
        cls,
        *,
        as_of: datetime,
        timeout: int = 15,
        sse_universe_fixture_path: str | Path | None = None,
        szse_universe_fixture_path: str | Path | None = None,
        tracking_index_fixture_path: str | Path | None = None,
        szse_relay_base_url: str | None = DEFAULT_SZSE_RELAY_BASE_URL,
        szse_relay_max_age_seconds: int = DEFAULT_RELAY_MAX_AGE_SECONDS,
    ) -> "LofRuntimeSession":
        universe = fetch_all_lof_universe(
            timeout=timeout,
            sse_fixture_path=sse_universe_fixture_path,
            szse_fixture_path=szse_universe_fixture_path,
            szse_relay_base_url=szse_relay_base_url,
            as_of=as_of,
            szse_relay_max_age_seconds=szse_relay_max_age_seconds,
        )
        uses_szse_relay = any(
            row.exchange == "SZSE"
            and row.source == "SZSE_OFFICIAL_RELAY"
            for row in universe
        )
        relay_bundle: SzseRelayBundle | None = None
        if uses_szse_relay:
            if not szse_relay_base_url:
                raise ValueError("SZSE relay transport selected without base URL")
            relay_bundle = fetch_szse_relay_bundle(
                szse_relay_base_url,
                timeout=max(timeout, 20),
            )
            validate_szse_relay_freshness(
                relay_bundle,
                as_of=as_of,
                max_age_seconds=szse_relay_max_age_seconds,
            )

        raw_type_map = fetch_fund_type_map(timeout=max(timeout, 20))
        type_records = classify_universe(
            universe,
            type_map=raw_type_map,
        )
        previous_trading_day = fetch_previous_trading_day(
            as_of=as_of.date(),
            timeout=timeout,
        )
        second_previous_trading_day = fetch_previous_trading_day(
            as_of=previous_trading_day,
            timeout=timeout,
        )
        tracking_index_override = (
            load_tracking_index_fixture(tracking_index_fixture_path)
            if tracking_index_fixture_path is not None
            else None
        )
        context_build = build_estimated_nav_context(
            universe=universe,
            type_records=type_records,
            previous_trading_day=previous_trading_day,
            tracking_index_override=tracking_index_override,
            timeout=timeout,
        )
        return cls(
            universe=universe,
            type_records=type_records,
            estimated_nav_context=context_build.context,
            context_build=context_build,
            context_built_at=as_of,
            second_previous_trading_day=second_previous_trading_day,
            szse_transport=(
                "OFFICIAL_RELAY"
                if uses_szse_relay
                else "DIRECT_OFFICIAL"
            ),
            szse_relay_bundle=relay_bundle,
        )

    def collect(
        self,
        *,
        generated_at: datetime,
        market_cutoff: datetime,
        max_quote_age_seconds: int = 60,
        timeout: int = 15,
        snapshot_id: str | None = None,
        official_nav_override: list[OfficialNavRecord] | None = None,
        trade_states_override: list[FundTradeStateRecord] | None = None,
    ) -> dict:
        return collect_market_snapshot(
            generated_at=generated_at,
            market_cutoff=market_cutoff,
            max_quote_age_seconds=max_quote_age_seconds,
            timeout=timeout,
            estimated_nav_context=self.estimated_nav_context,
            previous_trading_day=self.estimated_nav_context.previous_trading_day,
            second_previous_trading_day=self.second_previous_trading_day,
            universe_override=self.universe,
            type_records_override=self.type_records,
            official_nav_override=official_nav_override,
            trade_states_override=trade_states_override,
            szse_relay_bundle=self.szse_relay_bundle,
            snapshot_id=snapshot_id,
        )

    def collect_and_persist(
        self,
        *,
        store: LofSnapshotStore,
        generated_at: datetime,
        market_cutoff: datetime,
        max_quote_age_seconds: int = 60,
        timeout: int = 15,
        snapshot_id: str | None = None,
        official_nav_override: list[OfficialNavRecord] | None = None,
        trade_states_override: list[FundTradeStateRecord] | None = None,
    ) -> Path:
        snapshot = self.collect(
            generated_at=generated_at,
            market_cutoff=market_cutoff,
            max_quote_age_seconds=max_quote_age_seconds,
            timeout=timeout,
            snapshot_id=snapshot_id,
            official_nav_override=official_nav_override,
            trade_states_override=trade_states_override,
        )
        return store.persist(snapshot)
