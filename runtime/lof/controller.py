from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from .classification import (
    FundTypeRecord,
    classify_universe,
    fetch_fund_type_map,
)
from .estimated_nav_lane import EstimatedNavContext, resolve_estimated_nav_lane
from .nav import OfficialNavRecord, fetch_all_official_nav
from .quote import fetch_quotes
from .snapshot import build_market_snapshot
from .state import FundTradeStateRecord, fetch_all_trade_states
from .universe import LofIdentity, fetch_all_lof_universe

if TYPE_CHECKING:
    from .szse_relay import SzseRelayBundle


def _error_name(exc: Exception) -> str:
    return f"{type(exc).__name__}:{str(exc)[:160]}"


def collect_market_snapshot(
    *,
    generated_at: datetime,
    market_cutoff: datetime,
    max_quote_age_seconds: int,
    timeout: int = 15,
    state_max_workers: int = 8,
    nav_max_workers: int = 8,
    quote_batch_size: int = 60,
    estimated_nav_context: EstimatedNavContext | None = None,
    previous_trading_day: date | None = None,
    second_previous_trading_day: date | None = None,
    universe_override: list[LofIdentity] | None = None,
    type_records_override: dict[tuple[str, str], FundTypeRecord] | None = None,
    official_nav_override: list[OfficialNavRecord] | None = None,
    trade_states_override: list[FundTradeStateRecord] | None = None,
    szse_relay_bundle: "SzseRelayBundle | None" = None,
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Collect one all-market LOF snapshot.

    Universe is the hard prerequisite. Other lanes degrade independently so the
    full-market table remains visible with explicit unavailable fields.
    """
    if universe_override is not None:
        universe = list(universe_override)
    else:
        try:
            universe = fetch_all_lof_universe(timeout=timeout)
        except Exception as exc:
            raise RuntimeError(
                f"LOF_UNIVERSE_FETCH_FAILED:{_error_name(exc)}"
            ) from exc

    lane_errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            "quote": pool.submit(
                fetch_quotes,
                universe,
                timeout=timeout,
                batch_size=quote_batch_size,
            ),
            **(
                {}
                if official_nav_override is not None
                else {
                    "official_nav": pool.submit(
                        fetch_all_official_nav,
                        universe,
                        timeout=timeout,
                        szse_max_workers=nav_max_workers,
                        szse_relay_bundle=szse_relay_bundle,
                    )
                }
            ),
            **(
                {}
                if trade_states_override is not None
                else {
                    "trade_state": pool.submit(
                        fetch_all_trade_states,
                        universe,
                        timeout=timeout,
                        max_workers=state_max_workers,
                    )
                }
            ),
            **(
                {}
                if type_records_override is not None
                else {
                    "type": pool.submit(
                        fetch_fund_type_map,
                        timeout=max(timeout, 20),
                    )
                }
            ),
        }

        lane_results: dict[str, Any] = {}
        for lane, future in futures.items():
            try:
                lane_results[lane] = future.result()
            except Exception as exc:
                lane_errors[lane] = _error_name(exc)
                lane_results[lane] = {} if lane == "type" else []

    if official_nav_override is not None:
        lane_results["official_nav"] = list(official_nav_override)
    if trade_states_override is not None:
        lane_results["trade_state"] = list(trade_states_override)

    if type_records_override is not None:
        type_records = dict(type_records_override)
    else:
        type_records = classify_universe(
            universe,
            type_map=lane_results["type"],
        )

    estimated_navs = []
    if estimated_nav_context is not None:
        try:
            estimated_navs = resolve_estimated_nav_lane(
                official_navs=lane_results["official_nav"],
                context=estimated_nav_context,
                as_of=market_cutoff,
                timeout=timeout,
            )
        except Exception as exc:
            lane_errors["estimated_nav"] = _error_name(exc)
            estimated_navs = []

    snapshot = build_market_snapshot(
        universe=universe,
        quotes=lane_results["quote"],
        official_navs=lane_results["official_nav"],
        estimated_navs=estimated_navs,
        trade_states=lane_results["trade_state"],
        type_records=type_records,
        generated_at=generated_at,
        market_cutoff=market_cutoff,
        max_quote_age_seconds=max_quote_age_seconds,
        previous_trading_day=previous_trading_day,
        second_previous_trading_day=second_previous_trading_day,
        snapshot_id=snapshot_id,
    )

    snapshot["collector_status"] = "DEGRADED" if lane_errors else "PASS"
    snapshot["lane_errors"] = lane_errors
    return snapshot
