from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from .classification import classify_universe, fetch_fund_type_map
from .nav import fetch_all_official_nav
from .quote import fetch_quotes
from .snapshot import build_market_snapshot
from .state import fetch_all_trade_states
from .universe import fetch_all_lof_universe


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
    snapshot_id: str | None = None,
) -> dict[str, Any]:
    """Collect one all-market LOF snapshot.

    Universe is the hard prerequisite. Other lanes degrade independently so the
    full-market table remains visible with explicit unavailable fields.
    """
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
            "official_nav": pool.submit(
                fetch_all_official_nav,
                universe,
                timeout=timeout,
                szse_max_workers=nav_max_workers,
            ),
            "trade_state": pool.submit(
                fetch_all_trade_states,
                universe,
                timeout=timeout,
                max_workers=state_max_workers,
            ),
            "type": pool.submit(
                fetch_fund_type_map,
                timeout=max(timeout, 20),
            ),
        }

        lane_results: dict[str, Any] = {}
        for lane, future in futures.items():
            try:
                lane_results[lane] = future.result()
            except Exception as exc:
                lane_errors[lane] = _error_name(exc)
                lane_results[lane] = {} if lane == "type" else []

    type_records = classify_universe(
        universe,
        type_map=lane_results["type"],
    )

    snapshot = build_market_snapshot(
        universe=universe,
        quotes=lane_results["quote"],
        official_navs=lane_results["official_nav"],
        trade_states=lane_results["trade_state"],
        type_records=type_records,
        generated_at=generated_at,
        market_cutoff=market_cutoff,
        max_quote_age_seconds=max_quote_age_seconds,
        snapshot_id=snapshot_id,
    )

    snapshot["collector_status"] = "DEGRADED" if lane_errors else "PASS"
    snapshot["lane_errors"] = lane_errors
    return snapshot
