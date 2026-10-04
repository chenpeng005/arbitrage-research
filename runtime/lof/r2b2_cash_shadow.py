"""R2-B2 cash-heavy shadow-only sampler; never feeds main estimated NAV."""

from __future__ import annotations

import argparse
from datetime import datetime, time as clock_time
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
    is_cash_heavy_candidate,
)
from .r2_fund_events import (
    DistributionSchedule,
    DistributionStore,
    cash_distribution_on,
    schedules_need_refresh,
)
from .r2a_holdings import (
    HoldingsSnapshot,
    fetch_latest_full_snapshot,
)
from .r2a_shadow import LiveQuote, fetch_live_quotes
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
METHOD = "R2B2_CASH_HEAVY_HOLDINGS_BASKET"
CONTRACT_VERSION = "R2B2_CASH_HEAVY_SHADOW_V1"
PROFILE_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "r2b2_cash_shadow_profiles_v0.json"
)


def _load_profiles() -> dict[str, dict]:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    rows = payload.get("candidates") or {}
    result: dict[str, dict] = {}
    for code, value in rows.items():
        row = dict(value)
        row["fund_code"] = str(code)
        row["min_disclosed_weight"] = Decimal(
            str(row.get("min_disclosed_weight") or "0.40")
        )
        result[str(code)] = row
    return result


PROFILES = _load_profiles()


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


def _market_probe_window(now: datetime) -> bool:
    local = now
    if local.tzinfo is None:
        local = local.replace(tzinfo=SHANGHAI_TZ)
    local = local.astimezone(SHANGHAI_TZ)
    if local.weekday() >= 5:
        return False
    value = local.time()
    return (
        clock_time(9, 25) <= value <= clock_time(11, 35)
        or clock_time(12, 55) <= value <= clock_time(15, 5)
    )


def _main_row(snapshot: dict, fund_code: str) -> dict | None:
    return next(
        (
            row
            for row in (snapshot.get("rows") or [])
            if str(row.get("code") or "") == fund_code
        ),
        None,
    )


def _unavailable(
    *,
    fund_code: str,
    profile: dict,
    row: dict | None,
    holdings: HoldingsSnapshot | None,
    allocation: AssetAllocationSnapshot | None,
    error: str,
) -> dict:
    return {
        "fund_code": fund_code,
        "fund_name": (row or {}).get("name") or profile["name"],
        "method": METHOD,
        "status": "UNAVAILABLE",
        "quality_candidate": profile["quality_candidate"],
        "research_group": "R2-B2-CASH_HEAVY",
        "economic_priority": profile.get("economic_priority"),
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
        "backtest": profile.get("backtest"),
        "error": error,
    }


def calculate_shadow_row(
    *,
    fund_code: str,
    profile: dict,
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
    row = _main_row(main_snapshot, fund_code)
    if row is None:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=None,
            holdings=holdings,
            allocation=allocation,
            error="FUND_NOT_IN_MAIN_SNAPSHOT",
        )
    if holdings is None:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=None,
            allocation=allocation,
            error="HOLDINGS_UNAVAILABLE",
        )
    if allocation is None:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=None,
            error="ASSET_ALLOCATION_UNAVAILABLE",
        )
    if distribution is None:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="DISTRIBUTION_SCHEDULE_UNAVAILABLE",
        )

    official_nav = _decimal(row.get("official_nav"))
    if official_nav is None:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="OFFICIAL_NAV_UNAVAILABLE",
        )
    if row.get("official_nav_lag_label") != "T-1":
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"OFFICIAL_NAV_NOT_T1:{row.get('official_nav_lag_label')}",
        )

    holdings_age = (as_of.date() - holdings.as_of_date).days
    if holdings_age < 0 or holdings_age > max_holdings_age_days:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"HOLDINGS_TOO_OLD:{holdings_age}",
        )
    allocation_age = (as_of.date() - allocation.as_of_date).days
    if allocation_age < 0 or allocation_age > max_allocation_age_days:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"ASSET_ALLOCATION_TOO_OLD:{allocation_age}",
        )
    if not is_cash_heavy_candidate(allocation):
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=allocation,
            error="ASSET_ALLOCATION_NOT_CASH_HEAVY",
        )

    min_disclosed_weight = profile["min_disclosed_weight"]
    if holdings.total_weight < min_disclosed_weight:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
            row=row,
            holdings=holdings,
            allocation=allocation,
            error=f"DISCLOSED_WEIGHT_TOO_LOW:{holdings.total_weight}",
        )
    if abs(holdings.total_weight - allocation.stock_weight) > max_weight_gap:
        return _unavailable(
            fund_code=fund_code,
            profile=profile,
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
            fund_code=fund_code,
            profile=profile,
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
        asset_return = (
            quote.current / quote.previous_close - Decimal("1")
        )
        estimated_return += item.nav_weight * asset_return
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
            fund_code=fund_code,
            profile=profile,
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
            fund_code=fund_code,
            profile=profile,
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
        "fund_code": fund_code,
        "fund_name": row.get("name") or profile["name"],
        "method": METHOD,
        "status": status,
        "quality_candidate": profile["quality_candidate"],
        "research_group": "R2-B2-CASH_HEAVY",
        "economic_priority": profile.get("economic_priority"),
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
        "backtest": profile.get("backtest"),
        "error": error,
    }


def _holdings_path(state_root: str | Path, fund_code: str) -> Path:
    return Path(state_root) / f"r2b2_{fund_code}_holdings.json"


def _load_holdings(
    state_root: str | Path,
    fund_code: str,
) -> HoldingsSnapshot | None:
    path = _holdings_path(state_root, fund_code)
    if not path.is_file():
        return None
    return HoldingsSnapshot.from_dict(
        json.loads(path.read_text(encoding="utf-8"))
    )


def _persist_holdings(
    state_root: str | Path,
    holdings: HoldingsSnapshot,
) -> None:
    path = _holdings_path(state_root, holdings.fund_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(
        path,
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


def persist_shadow(
    state_root: str | Path,
    snapshot: dict,
) -> Path:
    root = Path(state_root)
    archive = root / "snapshots" / f"{snapshot['snapshot_id']}.json"
    latest = root / "r2b2_cash_shadow.json"
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

    holdings_by_fund: dict[str, HoldingsSnapshot] = {}
    holdings_refresh_errors: dict[str, str] = {}
    for code, profile in PROFILES.items():
        holdings = _load_holdings(state_root, code)
        if (
            holdings is None
            or _needs_refresh(
                holdings.fetched_at,
                now=now,
                refresh_seconds=holdings_refresh_seconds,
            )
        ):
            try:
                holdings = fetch_latest_full_snapshot(
                    code,
                    now=now,
                    timeout=timeout,
                    min_total_weight=profile["min_disclosed_weight"],
                    previous=holdings,
                )
                _persist_holdings(state_root, holdings)
            except Exception as exc:
                holdings_refresh_errors[code] = (
                    f"{type(exc).__name__}:{str(exc)[:180]}"
                )
        if holdings is not None:
            holdings_by_fund[code] = holdings

    allocation_store = AssetAllocationStore(state_root)
    allocations = allocation_store.load()
    allocation_refresh_errors: dict[str, str] = {}
    for code in PROFILES:
        allocation = allocations.get(code)
        if (
            allocation is None
            or _needs_refresh(
                allocation.fetched_at,
                now=now,
                refresh_seconds=allocation_refresh_seconds,
            )
        ):
            try:
                allocation = fetch_asset_allocation(
                    code,
                    now=now,
                    timeout=timeout,
                )
                allocations[code] = allocation
            except Exception as exc:
                allocation_refresh_errors[code] = (
                    f"{type(exc).__name__}:{str(exc)[:180]}"
                )
    allocation_store.persist(
        {
            code: allocations[code]
            for code in PROFILES
            if code in allocations
        },
        generated_at=now,
    )

    distribution_store = DistributionStore(state_root)
    distributions = distribution_store.load()
    distribution_refresh_errors: dict[str, str] = {}
    if schedules_need_refresh(
        distributions,
        PROFILES.keys(),
        now=now,
        refresh_seconds=distribution_refresh_seconds,
    ):
        distributions, distribution_refresh_errors = (
            distribution_store.refresh(
                PROFILES.keys(),
                now=now,
                timeout=timeout,
            )
        )

    symbols = sorted(
        {
            item.symbol
            for holdings in holdings_by_fund.values()
            for item in holdings.holdings
        }
    )
    quotes = fetch_live_quotes(symbols, timeout=timeout)

    rows = [
        calculate_shadow_row(
            fund_code=code,
            profile=profile,
            main_snapshot=main_snapshot,
            holdings=holdings_by_fund.get(code),
            allocation=allocations.get(code),
            distribution=distributions.get(code),
            quotes=quotes,
            as_of=now,
            max_quote_age_seconds=max_quote_age_seconds,
        )
        for code, profile in sorted(PROFILES.items())
    ]

    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "r2b2-cash-shadow-" + now.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "method": METHOD,
        "candidate_count": len(PROFILES),
        "summary": {
            "available_count": sum(x["status"] == "AVAILABLE" for x in rows),
            "stale_count": sum(x["status"] == "STALE" for x in rows),
            "unavailable_count": sum(
                x["status"] == "UNAVAILABLE" for x in rows
            ),
        },
        "holdings_refresh_errors": holdings_refresh_errors,
        "allocation_refresh_errors": allocation_refresh_errors,
        "distribution_refresh_errors": distribution_refresh_errors,
        "rows": rows,
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
        active = any(
            (
                _main_row(main_snapshot or {}, code) or {}
            ).get("quote_status") == "FRESH"
            for code in PROFILES
        )
        probe_window = _market_probe_window(now)
        interval = (
            quote_interval_seconds
            if active or probe_window
            else off_hours_interval_seconds
        )
        try:
            if active or not probe_window:
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
                            "event": "r2b2_cash_shadow_persisted",
                            "time": now.isoformat(),
                            "snapshot_id": snapshot["snapshot_id"],
                            "summary": snapshot["summary"],
                            "active_sampling": active,
                            "market_probe_window": probe_window,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                print(
                    json.dumps(
                        {
                            "event": "r2b2_cash_shadow_idle",
                            "time": now.isoformat(),
                            "reason": "WAITING_FOR_MAIN_MARKET_FRESH",
                            "active_sampling": False,
                            "market_probe_window": True,
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": "r2b2_cash_shadow_failed",
                        "time": now.isoformat(),
                        "error": (
                            f"{type(exc).__name__}:"
                            f"{str(exc)[:240]}"
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
                "rows": snapshot["rows"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
