from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .classification import FundTypeRecord, classify_universe, fetch_fund_type_map
from .context_builder import (
    EstimatedNavContextBuildResult,
    build_estimated_nav_context,
)
from .controller import collect_market_snapshot
from .estimated_nav_lane import EstimatedNavContext
from .snapshot_store import LofSnapshotStore
from .trading_calendar import fetch_previous_trading_day
from .universe import LofIdentity, fetch_all_lof_universe


@dataclass
class LofRuntimeSession:
    """Holds low-frequency LOF metadata for repeated high-frequency snapshots."""

    universe: list[LofIdentity]
    type_records: dict[tuple[str, str], FundTypeRecord]
    estimated_nav_context: EstimatedNavContext
    context_build: EstimatedNavContextBuildResult
    context_built_at: datetime

    @classmethod
    def build(
        cls,
        *,
        as_of: datetime,
        timeout: int = 15,
        sse_universe_fixture_path: str | Path | None = None,
        szse_universe_fixture_path: str | Path | None = None,
    ) -> "LofRuntimeSession":
        universe = fetch_all_lof_universe(
            timeout=timeout,
            sse_fixture_path=sse_universe_fixture_path,
            szse_fixture_path=szse_universe_fixture_path,
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
        context_build = build_estimated_nav_context(
            universe=universe,
            type_records=type_records,
            previous_trading_day=previous_trading_day,
            timeout=timeout,
        )
        return cls(
            universe=universe,
            type_records=type_records,
            estimated_nav_context=context_build.context,
            context_build=context_build,
            context_built_at=as_of,
        )

    def collect(
        self,
        *,
        generated_at: datetime,
        market_cutoff: datetime,
        max_quote_age_seconds: int = 60,
        timeout: int = 15,
        snapshot_id: str | None = None,
    ) -> dict:
        return collect_market_snapshot(
            generated_at=generated_at,
            market_cutoff=market_cutoff,
            max_quote_age_seconds=max_quote_age_seconds,
            timeout=timeout,
            estimated_nav_context=self.estimated_nav_context,
            universe_override=self.universe,
            type_records_override=self.type_records,
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
    ) -> Path:
        snapshot = self.collect(
            generated_at=generated_at,
            market_cutoff=market_cutoff,
            max_quote_age_seconds=max_quote_age_seconds,
            timeout=timeout,
            snapshot_id=snapshot_id,
        )
        return store.persist(snapshot)
