"""R2-B2 low-vol residual shadow-only sampler for 165508.

The residual is deliberately not priced with a generic bond proxy. Historical
cross-source validation showed the disclosed stock basket already explains NAV
changes with low error. This module therefore treats the validated residual as
zero-return only inside Shadow, never in main estimated NAV.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile
import time
from zoneinfo import ZoneInfo

from .r2_asset_allocation import (
    AssetAllocationSnapshot,
    AssetAllocationStore,
    fetch_asset_allocation,
)
from .r2_fund_events import (
    DistributionSchedule,
    DistributionStore,
    cash_distribution_on,
    schedules_need_refresh,
)
from .r2a_holdings import HoldingsSnapshot, fetch_latest_full_snapshot
from .r2a_shadow import LiveQuote, fetch_live_quotes
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FUND_CODE = "165508"
FUND_NAME = "中信保诚深度LOF"
METHOD = "R2B2_LOW_VOL_RESIDUAL_STOCK_BASKET"
CONTRACT_VERSION = "R2B2_LOW_VOL_RESIDUAL_SHADOW_V0"
PROFILE_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "r2b2_low_vol_residual_profile_v0.json"
)


def _load_profile() -> dict:
    value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    value["min_disclosed_weight"] = Decimal(
        str(value.get("min_disclosed_weight") or "0.70")
    )
    return value


PROFILE = _load_profile()


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result > 0 else None


def _quote_age(quote_time: datetime | None, as_of: datetime) -> int | None:
    if quote_time is None:
        return None
    if quote_time.tzinfo is None:
        quote_time = quote_time.replace(tzinfo=SHANGHAI_TZ)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=SHANGHAI_TZ)
    return int(
        (
            as_of.astimezone(SHANGHAI_TZ)
            - quote_time.astimezone(SHANGHAI_TZ)
        ).total_seconds()
    )


def _main_row(snapshot: dict) -> dict | None:
    return next(
        (
            row
            for row in (snapshot.get("rows") or [])
            if str(row.get("code") or "") == FUND_CODE
        ),
        None,
    )


def is_validated_low_vol_residual(
    allocation: AssetAllocationSnapshot,
) -> bool:
    return (
        Decimal("0.65") <= allocation.stock_weight <= Decimal("0.80")
        and Decimal("0.08") <= allocation.bond_weight <= Decimal("0.20")
        and allocation.cash_weight >= Decimal("0.10")
    )


def _unavailable(
    *,
    row: dict | None,
    holdings: HoldingsSnapshot | None,
    allocation: AssetAllocationSnapshot | None,
    error: str,
) -> dict:
    return {
        "fund_code": FUND_CODE,
        "fund_name": (row or {}).get("name") or FUND_NAME,
        "method": METHOD,
        "status": "UNAVAILABLE",
        "quality_candidate": PROFILE["quality_candidate"],
        "research_group": PROFILE["research_group"],
        "economic_priority": PROFILE["economic_priority"],
        "eligible_for_main": False,
        "shadow_estimated_nav": None,
        "shadow_premium_rate": None,
        "estimated_return": None,
        "official_nav": (row or {}).get("official_nav"),
        "official_nav_date": (row or {}).get("official_nav_date"),
        "market_price": (row or {}).get("price"),
        "market_quote_status": (row or {}).get("quote_status"),
        "holdings_as_of_date": (
            holdings.as_of_date.isoformat() if holdings else None
        ),
        "disclosed_stock_weight": (
            float(holdings.total_weight) if holdings else None
        ),
        "asset_allocation_date": (
            allocation.as_of_date.isoformat() if allocation else None
        ),
        "allocation_stock_weight": (
            float(allocation.stock_weight) if allocation else None
        ),
        "allocation_bond_weight": (
            float(allocation.bond_weight) if allocation else None
        ),
        "allocation_cash_weight": (
            float(allocation.cash_weight) if allocation else None
        ),
        "live_coverage_ratio": None,
        "fresh_coverage_ratio": None,
        "cash_distribution_per_unit": None,
        "cross_source_backtest": PROFILE["backtest"],
        "residual_model": (
            "validated low-vol residual; no generic bond proxy"
        ),
        "error": error,
    }


def calculate_shadow_row(
    *,
    main_snapshot: dict,
    holdings: HoldingsSnapshot | None,
    allocation: AssetAllocationSnapshot | None,
    distribution: DistributionSchedule | None,
    quotes: dict[str, LiveQuote],
    as_of: datetime,
    max_quote_age_seconds: int = 120,
    min_live_ratio: Decimal = Decimal("0.98"),
    max_holdings_age_days: int = 130,
    max_allocation_age_days: int = 130,
    max_distribution_age_seconds: int = 86400,
    max_weight_gap: Decimal = Decimal("0.03"),
) -> dict:
    row = _main_row(main_snapshot)
    if row is None:
        return _unavailable(
            row=None,
            holdings=holdings,
            allocation=allocation,
            error="FUND_NOT_IN_MAIN_SNAPSHOT",
        )
    if holdings is None:
        return _unavailable(
            row=row,
            holdings=None,
            allocation=allocation,
            error="HOLDINGS_UNAVAILABLE",
        )
    if allocation is None:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=None,
            error="ASSET_ALLOCATION_UNAVAILABLE",
        )
    if distribution is None:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="DISTRIBUTION_SCHEDULE_UNAVAILABLE",
        )

    official_nav = _decimal(row.get("official_nav"))
    if official_nav is None:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="OFFICIAL_NAV_UNAVAILABLE",
        )
    if row.get("official_nav_lag_label") != "T-1":
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"OFFICIAL_NAV_NOT_T1:{row.get('official_nav_lag_label')}",
        )

    holdings_age = (as_of.date() - holdings.as_of_date).days
    if holdings_age < 0 or holdings_age > max_holdings_age_days:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"HOLDINGS_TOO_OLD:{holdings_age}",
        )
    allocation_age = (as_of.date() - allocation.as_of_date).days
    if allocation_age < 0 or allocation_age > max_allocation_age_days:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"ASSET_ALLOCATION_TOO_OLD:{allocation_age}",
        )
    if not is_validated_low_vol_residual(allocation):
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="ASSET_ALLOCATION_NOT_VALIDATED_RESIDUAL",
        )
    if holdings.total_weight < PROFILE["min_disclosed_weight"]:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"DISCLOSED_WEIGHT_TOO_LOW:{holdings.total_weight}",
        )
    if abs(holdings.total_weight - allocation.stock_weight) > max_weight_gap:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="HOLDINGS_ALLOCATION_WEIGHT_MISMATCH",
        )

    dist_age = _quote_age(distribution.fetched_at, as_of)
    if (
        dist_age is None
        or dist_age < -300
        or dist_age > max_distribution_age_seconds
    ):
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="DISTRIBUTION_SCHEDULE_STALE",
        )
    cash_distribution = cash_distribution_on(distribution, as_of.date())

    estimated_return = Decimal("0")
    valid_weight = Decimal("0")
    fresh_weight = Decimal("0")
    used_times: list[datetime] = []
    for item in holdings.holdings:
        quote = quotes.get(item.symbol)
        if quote is None or not quote.available:
            continue
        assert quote.current is not None
        assert quote.previous_close is not None
        assert quote.quote_time is not None
        estimated_return += item.nav_weight * (
            quote.current / quote.previous_close - Decimal("1")
        )
        valid_weight += item.nav_weight
        used_times.append(quote.quote_time)
        age = _quote_age(quote.quote_time, as_of)
        if age is not None and -300 <= age <= max_quote_age_seconds:
            fresh_weight += item.nav_weight

    disclosed = holdings.total_weight
    live_ratio = valid_weight / disclosed
    fresh_ratio = fresh_weight / disclosed
    if live_ratio < min_live_ratio:
        result = _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"LIVE_COVERAGE_TOO_LOW:{live_ratio}",
        )
        result["live_coverage_ratio"] = float(live_ratio)
        result["fresh_coverage_ratio"] = float(fresh_ratio)
        return result

    status = "AVAILABLE" if fresh_ratio >= min_live_ratio else "STALE"
    error = None if status == "AVAILABLE" else "STALE_HOLDINGS_QUOTES"
    estimated_nav = (
        official_nav * (Decimal("1") + estimated_return)
        - cash_distribution
    )
    if estimated_nav <= 0:
        return _unavailable(
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="NON_POSITIVE_ESTIMATED_NAV",
        )

    market_price = _decimal(row.get("price"))
    premium = None
    if (
        status == "AVAILABLE"
        and row.get("quote_status") == "FRESH"
        and market_price is not None
    ):
        premium = (
            market_price / estimated_nav - Decimal("1")
        ) * Decimal("100")

    return {
        "fund_code": FUND_CODE,
        "fund_name": row.get("name") or FUND_NAME,
        "method": METHOD,
        "status": status,
        "quality_candidate": PROFILE["quality_candidate"],
        "research_group": PROFILE["research_group"],
        "economic_priority": PROFILE["economic_priority"],
        "eligible_for_main": False,
        "shadow_estimated_nav": float(estimated_nav),
        "shadow_premium_rate": (
            float(premium) if premium is not None else None
        ),
        "estimated_return": float(estimated_return),
        "official_nav": float(official_nav),
        "official_nav_date": row.get("official_nav_date"),
        "market_price": (
            float(market_price) if market_price is not None else None
        ),
        "market_quote_status": row.get("quote_status"),
        "holdings_as_of_date": holdings.as_of_date.isoformat(),
        "disclosed_stock_weight": float(disclosed),
        "asset_allocation_date": allocation.as_of_date.isoformat(),
        "allocation_stock_weight": float(allocation.stock_weight),
        "allocation_bond_weight": float(allocation.bond_weight),
        "allocation_cash_weight": float(allocation.cash_weight),
        "live_coverage_ratio": float(live_ratio),
        "fresh_coverage_ratio": float(fresh_ratio),
        "cash_distribution_per_unit": float(cash_distribution),
        "quote_time_min": (
            min(used_times).isoformat() if used_times else None
        ),
        "quote_time_max": (
            max(used_times).isoformat() if used_times else None
        ),
        "cross_source_backtest": PROFILE["backtest"],
        "residual_model": "validated low-vol residual; no generic bond proxy",
        "error": error,
    }


def _holdings_path(state_root: str | Path) -> Path:
    return Path(state_root) / "r2b2_165508_holdings.json"


def _load_holdings(state_root: str | Path) -> HoldingsSnapshot | None:
    path = _holdings_path(state_root)
    if not path.is_file():
        return None
    return HoldingsSnapshot.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _persist_holdings(
    state_root: str | Path,
    holdings: HoldingsSnapshot,
) -> None:
    _atomic_write(
        _holdings_path(state_root),
        json.dumps(
            holdings.to_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
    )


def _needs_refresh(
    fetched_at: datetime,
    *,
    now: datetime,
    refresh_seconds: int,
) -> bool:
    current = now
    fetched = fetched_at
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=SHANGHAI_TZ)
    age = (
        current.astimezone(SHANGHAI_TZ)
        - fetched.astimezone(SHANGHAI_TZ)
    ).total_seconds()
    return age < 0 or age >= refresh_seconds


def persist_shadow(
    state_root: str | Path,
    snapshot: dict,
) -> Path:
    root = Path(state_root)
    archive = root / "snapshots" / f"{snapshot['snapshot_id']}.json"
    latest = root / "r2b2_low_vol_165508_shadow.json"
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    ) + "\n"
    _atomic_write(archive, payload)
    _atomic_write(latest, payload)
    return archive


def collect_once(
    *,
    main_data_root: str | Path,
    state_root: str | Path,
    now: datetime | None = None,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    holdings_refresh_seconds: int = 21600,
    allocation_refresh_seconds: int = 21600,
    distribution_refresh_seconds: int = 21600,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(main_data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    holdings = _load_holdings(state_root)
    if (
        holdings is None
        or _needs_refresh(
            holdings.fetched_at,
            now=now,
            refresh_seconds=holdings_refresh_seconds,
        )
    ):
        holdings = fetch_latest_full_snapshot(
            FUND_CODE,
            now=now,
            timeout=timeout,
            min_total_weight=PROFILE["min_disclosed_weight"],
            previous=holdings,
        )
        _persist_holdings(state_root, holdings)

    allocation_store = AssetAllocationStore(state_root)
    allocations = allocation_store.load()
    allocation = allocations.get(FUND_CODE)
    if (
        allocation is None
        or _needs_refresh(
            allocation.fetched_at,
            now=now,
            refresh_seconds=allocation_refresh_seconds,
        )
    ):
        allocation = fetch_asset_allocation(
            FUND_CODE,
            now=now,
            timeout=timeout,
        )
        allocation_store.persist(
            {FUND_CODE: allocation},
            generated_at=now,
        )

    distribution_store = DistributionStore(state_root)
    distributions = distribution_store.load()
    if schedules_need_refresh(
        distributions,
        [FUND_CODE],
        now=now,
        refresh_seconds=distribution_refresh_seconds,
    ):
        distributions, _ = distribution_store.refresh(
            [FUND_CODE],
            now=now,
            timeout=timeout,
        )
    distribution = distributions.get(FUND_CODE)

    quotes = fetch_live_quotes(
        [item.symbol for item in holdings.holdings],
        timeout=timeout,
    )
    row = calculate_shadow_row(
        main_snapshot=main_snapshot,
        holdings=holdings,
        allocation=allocation,
        distribution=distribution,
        quotes=quotes,
        as_of=now,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "r2b2-low-vol-165508-shadow-"
            + now.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "method": METHOD,
        "summary": {
            "available_count": int(row["status"] == "AVAILABLE"),
            "stale_count": int(row["status"] == "STALE"),
            "unavailable_count": int(row["status"] == "UNAVAILABLE"),
        },
        "rows": [row],
    }
    persist_shadow(state_root, snapshot)
    return snapshot


def run_loop(
    *,
    main_data_root: str | Path,
    state_root: str | Path,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    main_store = LofSnapshotStore(main_data_root)
    while True:
        started = time.monotonic()
        now = datetime.now(SHANGHAI_TZ)
        main_snapshot = main_store.load_latest()
        main_row = _main_row(main_snapshot or {})
        active = bool(
            main_row
            and main_row.get("quote_status") == "FRESH"
        )
        interval = (
            quote_interval_seconds
            if active
            else off_hours_interval_seconds
        )
        try:
            snapshot = collect_once(
                main_data_root=main_data_root,
                state_root=state_root,
                now=now,
                timeout=timeout,
                max_quote_age_seconds=max_quote_age_seconds,
            )
            print(
                json.dumps(
                    {
                        "event": "r2b2_low_vol_shadow_persisted",
                        "time": now.isoformat(),
                        "snapshot_id": snapshot["snapshot_id"],
                        "summary": snapshot["summary"],
                        "active_sampling": active,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "r2b2_low_vol_shadow_failed",
                        "time": now.isoformat(),
                        "error": (
                            f"{type(exc).__name__}:{str(exc)[:240]}"
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        elapsed = time.monotonic() - started
        time.sleep(max(0.0, interval - elapsed))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-data-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument("--max-quote-age-seconds", type=int, default=120)
    parser.add_argument("--quote-interval-seconds", type=float, default=30.0)
    parser.add_argument(
        "--off-hours-interval-seconds",
        type=float,
        default=300.0,
    )
    parser.add_argument("--loop", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.loop:
        return run_loop(
            main_data_root=args.main_data_root,
            state_root=args.state_root,
            timeout=args.timeout,
            max_quote_age_seconds=args.max_quote_age_seconds,
            quote_interval_seconds=args.quote_interval_seconds,
            off_hours_interval_seconds=args.off_hours_interval_seconds,
        )
    snapshot = collect_once(
        main_data_root=args.main_data_root,
        state_root=args.state_root,
        timeout=args.timeout,
        max_quote_age_seconds=args.max_quote_age_seconds,
    )
    print(
        json.dumps(
            {
                "snapshot_id": snapshot["snapshot_id"],
                "source_market_snapshot_id": snapshot[
                    "source_market_snapshot_id"
                ],
                "summary": snapshot["summary"],
                "row": snapshot["rows"][0],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
