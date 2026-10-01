[Reading 930 lines from start (total: 930 lines, 0 remaining)]

from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile
import time
from zoneinfo import ZoneInfo

from .r2_fund_events import (
    DistributionSchedule,
    DistributionStore,
    cash_distribution_on,
    schedules_need_refresh,
)
from .r2a_shadow import (
    LiveQuote,
    fetch_live_quotes,
)
from .r2c_risk_holdings import (
    RiskHoldingsSnapshot,
    RiskHoldingsStore,
    snapshots_need_refresh,
)
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
METHOD = "RISK_ASSET_OVERLAY"
CONTRACT_VERSION = "R2C_RISK_OVERLAY_SHADOW_V1"
REGISTRY_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "r2c_risk_overlay_registry_v0_1.json"
)


def load_registry() -> dict[str, dict]:
    payload = json.loads(
        REGISTRY_PATH.read_text(encoding="utf-8")
    )
    return {
        str(code): dict(value)
        for code, value
        in (payload.get("funds") or {}).items()
    }


def _decimal(value) -> Decimal | None:
    try:
        if value is None or value == "":
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _age_seconds(
    value: datetime | None,
    *,
    now: datetime,
) -> int | None:
    if value is None:
        return None
    current = now
    item = value
    if item.tzinfo is None and current.tzinfo is not None:
        item = item.replace(tzinfo=current.tzinfo)
    if current.tzinfo is None and item.tzinfo is not None:
        current = current.replace(tzinfo=item.tzinfo)
    try:
        return max(0, int((current - item).total_seconds()))
    except TypeError:
        return None


def _market_is_fresh(snapshot: dict) -> bool:
    quality = snapshot.get("quality_summary") or {}
    return int(quality.get("quote_fresh_count") or 0) > 0


def _schedule_is_fresh(
    schedule: DistributionSchedule | None,
    *,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    if schedule is None:
        return False
    age = _age_seconds(
        schedule.fetched_at,
        now=now,
    )
    return age is not None and age <= max_age_seconds


def _unavailable(
    code: str,
    profile: dict,
    *,
    market_row: dict | None,
    holdings: RiskHoldingsSnapshot | None,
    error: str,
) -> dict:
    return {
        "fund_code": code,
        "fund_name": (
            (market_row or {}).get("name")
            or profile.get("name")
            or code
        ),
        "method": METHOD,
        "driver": profile.get("driver"),
        "band_group": profile.get("band_group"),
        "status": "UNAVAILABLE",
        "quality_candidate": profile.get("quality_candidate"),
        "backtest_mae_pct": profile.get("backtest_mae_pct"),
        "backtest_p90_pct": profile.get("backtest_p90_pct"),
        "shadow_estimated_nav": None,
        "shadow_premium_rate": None,
        "estimated_return": None,
        "cash_distribution_per_unit": None,
        "official_nav": (market_row or {}).get("official_nav"),
        "official_nav_date": (
            market_row or {}
        ).get("official_nav_date"),
        "market_price": (market_row or {}).get("price"),
        "holdings_as_of_date": (
            holdings.as_of_date.isoformat()
            if holdings is not None
            else None
        ),
        "holdings_first_seen_at": (
            holdings.first_seen_at.isoformat()
            if holdings is not None
            else None
        ),
        "holdings_identity": (
            holdings.identity
            if holdings is not None
            else None
        ),
        "stock_weight": (
            float(holdings.stock_weight)
            if holdings is not None
            else None
        ),
        "cb_weight": (
            float(holdings.cb_weight)
            if holdings is not None
            else None
        ),
        "total_risk_weight": (
            float(holdings.total_risk_weight)
            if holdings is not None
            else None
        ),
        "valid_quote_weight": None,
        "fresh_quote_weight": None,
        "live_coverage_ratio": None,
        "fresh_coverage_ratio": None,
        "quote_time_min": None,
        "quote_time_max": None,
        "error": error,
    }


def calculate_rows(
    *,
    main_snapshot: dict,
    registry: dict[str, dict],
    holdings_by_fund: dict[str, RiskHoldingsSnapshot],
    distributions: dict[str, DistributionSchedule],
    quotes: dict[str, LiveQuote],
    as_of: datetime,
    max_quote_age_seconds: int = 120,
    max_distribution_age_seconds: int = 86400,
    max_holdings_age_days: int = 200,
    min_live_ratio: Decimal = Decimal("0.98"),
) -> list[dict]:
    market_rows = {
        str(row.get("code")): row
        for row in (main_snapshot.get("rows") or [])
    }
    result: list[dict] = []

    for code, profile in registry.items():
        market_row = market_rows.get(code)
        holdings = holdings_by_fund.get(code)

        if market_row is None:
            result.append(
                _unavailable(
                    code,
                    profile,
                    market_row=None,
                    holdings=holdings,
                    error="MISSING_MAIN_MARKET_ROW",
                )
            )
            continue
        if holdings is None:
            result.append(
                _unavailable(
                    code,
                    profile,
                    market_row=market_row,
                    holdings=None,
                    error="MISSING_RISK_HOLDINGS",
                )
            )
            continue

        official_nav = _decimal(
            market_row.get("official_nav")
        )
        nav_date = _parse_date(
            market_row.get("official_nav_date")
        )
        if (
            official_nav is None
            or official_nav <= 0
            or nav_date is None
        ):
            result.append(
                _unavailable(
                    code,
                    profile,
                    market_row=market_row,
                    holdings=holdings,
                    error="OFFICIAL_NAV_UNAVAILABLE",
                )
            )
            continue

        if market_row.get("official_nav_lag_label") != "T-1":
            result.append(
                _unavailable(
                    code,
                    profile,
                    market_row=market_row,
                    holdings=holdings,
                    error="OFFICIAL_NAV_NOT_T1",
                )
            )
            continue

        holdings_age = (
            as_of.date() - holdings.as_of_date
        ).days
        if (
            holdings_age < 0
            or holdings_age > max_holdings_age_days
        ):
            result.append(
                _unavailable(
                    code,
                    profile,
                    market_row=market_row,
                    holdings=holdings,
                    error=f"HOLDINGS_TOO_OLD:{holdings_age}",
                )
            )
            continue

        schedule = distributions.get(code)
        if not _schedule_is_fresh(
            schedule,
            now=as_of,
            max_age_seconds=max_distribution_age_seconds,
        ):
            result.append(
                _unavailable(
                    code,
                    profile,
                    market_row=market_row,
                    holdings=holdings,
                    error="DISTRIBUTION_SCHEDULE_STALE_OR_MISSING",
                )
            )
            continue
        assert schedule is not None

        risk_weight = holdings.total_risk_weight
        if risk_weight <= 0:
            result.append(
                _unavailable(
                    code,
                    profile,
                    market_row=market_row,
                    holdings=holdings,
                    error="NO_RISK_WEIGHT",
                )
            )
            continue

        estimated_return = Decimal("0")
        valid_weight = Decimal("0")
        fresh_weight = Decimal("0")
        quote_times: list[datetime] = []

        for position in holdings.positions:
            quote = quotes.get(position.symbol)
            if quote is None or not quote.available:
                continue
            assert quote.current is not None
            assert quote.previous_close is not None
            assert quote.quote_time is not None

            asset_return = (
                quote.current / quote.previous_close
                - Decimal("1")
            )
            estimated_return += (
                position.nav_weight * asset_return
            )
            valid_weight += position.nav_weight
            quote_times.append(quote.quote_time)

            age = _age_seconds(
                quote.quote_time,
                now=as_of,
            )
            if (
                age is not None
                and age <= max_quote_age_seconds
            ):
                fresh_weight += position.nav_weight

        live_ratio = valid_weight / risk_weight
        fresh_ratio = fresh_weight / risk_weight

        if live_ratio < min_live_ratio:
            row = _unavailable(
                code,
                profile,
                market_row=market_row,
                holdings=holdings,
                error=(
                    "LIVE_RISK_WEIGHT_COVERAGE_TOO_LOW:"
                    f"{live_ratio}"
                ),
            )
            row["valid_quote_weight"] = float(valid_weight)
            row["fresh_quote_weight"] = float(fresh_weight)
            row["live_coverage_ratio"] = float(live_ratio)
            row["fresh_coverage_ratio"] = float(fresh_ratio)
            result.append(row)
            continue

        status = (
            "AVAILABLE"
            if fresh_ratio >= min_live_ratio
            else "STALE"
        )

        distribution = cash_distribution_on(
            schedule,
            as_of.date(),
        )
        estimated_nav = (
            official_nav
            * (Decimal("1") + estimated_return)
            - distribution
        )

        market_price = _decimal(
            market_row.get("price")
        )
        premium = None
        if (
            market_price is not None
            and market_price > 0
            and estimated_nav > 0
        ):
            premium = (
                market_price / estimated_nav
                - Decimal("1")
            ) * Decimal("100")

        result.append(
            {
                "fund_code": code,
                "fund_name": (
                    market_row.get("name")
                    or profile.get("name")
                    or code
                ),
                "method": METHOD,
                "driver": profile.get("driver"),
                "band_group": profile.get("band_group"),
                "status": status,
                "quality_candidate": profile.get(
                    "quality_candidate"
                ),
                "backtest_mae_pct": profile.get(
                    "backtest_mae_pct"
                ),
                "backtest_p90_pct": profile.get(
                    "backtest_p90_pct"
                ),
                "shadow_estimated_nav": float(
                    estimated_nav
                ),
                "shadow_premium_rate": (
                    float(premium)
                    if premium is not None
                    else None
                ),
                "estimated_return": float(
                    estimated_return
                ),
                "cash_distribution_per_unit": float(
                    distribution
                ),
                "official_nav": float(official_nav),
                "official_nav_date": nav_date.isoformat(),
                "market_price": (
                    float(market_price)
                    if market_price is not None
                    else None
                ),
                "holdings_as_of_date": (
                    holdings.as_of_date.isoformat()
                ),
                "holdings_first_seen_at": (
                    holdings.first_seen_at.isoformat()
                ),
                "holdings_identity": holdings.identity,
                "stock_weight": float(
                    holdings.stock_weight
                ),
                "cb_weight": float(
                    holdings.cb_weight
                ),
                "total_risk_weight": float(
                    holdings.total_risk_weight
                ),
                "valid_quote_weight": float(
                    valid_weight
                ),
                "fresh_quote_weight": float(
                    fresh_weight
                ),
                "live_coverage_ratio": float(
                    live_ratio
                ),
                "fresh_coverage_ratio": float(
                    fresh_ratio
                ),
                "quote_time_min": (
                    min(quote_times).isoformat()
                    if quote_times
                    else None
                ),
                "quote_time_max": (
                    max(quote_times).isoformat()
                    if quote_times
                    else None
                ),
                "error": None,
            }
        )

    return result


def _low_frequency_root(
    data_root: str | Path,
) -> Path:
    return Path(data_root) / "r2c_risk_overlay"


def _load_or_refresh_low_frequency(
    *,
    data_root: str | Path,
    registry: dict[str, dict],
    now: datetime,
    timeout: int,
    refresh_seconds: int,
) -> tuple[
    dict[str, RiskHoldingsSnapshot],
    dict[str, DistributionSchedule],
    dict[str, str],
    dict[str, str],
]:
    root = _low_frequency_root(data_root)
    codes = registry.keys()

    holdings_store = RiskHoldingsStore(root)
    holdings = holdings_store.load()
    holdings_errors: dict[str, str] = {}
    if snapshots_need_refresh(
        holdings,
        codes,
        now=now,
        refresh_seconds=refresh_seconds,
    ):
        holdings, holdings_errors = (
            holdings_store.refresh(
                codes,
                now=now,
                timeout=timeout,
            )
        )

    distribution_store = DistributionStore(root)
    schedules = distribution_store.load()
    distribution_errors: dict[str, str] = {}
    if schedules_need_refresh(
        schedules,
        codes,
        now=now,
        refresh_seconds=refresh_seconds,
    ):
        schedules, distribution_errors = (
            distribution_store.refresh(
                codes,
                now=now,
                timeout=timeout,
            )
        )

    return (
        holdings,
        schedules,
        holdings_errors,
        distribution_errors,
    )


def collect_once(
    *,
    data_root: str | Path,
    now: datetime | None = None,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    low_frequency_refresh_seconds: int = 21600,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    registry = load_registry()

    main_snapshot = LofSnapshotStore(
        data_root
    ).load_latest()
    if main_snapshot is None:
        raise RuntimeError(
            "MAIN_SNAPSHOT_UNAVAILABLE"
        )

    (
        holdings,
        schedules,
        holdings_errors,
        distribution_errors,
    ) = _load_or_refresh_low_frequency(
        data_root=data_root,
        registry=registry,
        now=now,
        timeout=timeout,
        refresh_seconds=low_frequency_refresh_seconds,
    )

    symbols = {
        position.symbol
        for code in registry
        for position in (
            holdings.get(code).positions
            if holdings.get(code) is not None
            else ()
        )
    }
    quotes = fetch_live_quotes(
        symbols,
        timeout=timeout,
        batch_size=80,
    )

    rows = calculate_rows(
        main_snapshot=main_snapshot,
        registry=registry,
        holdings_by_fund=holdings,
        distributions=schedules,
        quotes=quotes,
        as_of=now,
        max_quote_age_seconds=max_quote_age_seconds,
    )

    summary = {
        "available_count": sum(
            x["status"] == "AVAILABLE"
            for x in rows
        ),
        "stale_count": sum(
            x["status"] == "STALE"
            for x in rows
        ),
        "unavailable_count": sum(
            x["status"] == "UNAVAILABLE"
            for x in rows
        ),
        "medium_candidate_count": sum(
            x.get("quality_candidate") == "MEDIUM"
            for x in rows
        ),
        "low_candidate_count": sum(
            x.get("quality_candidate") == "LOW"
            for x in rows
        ),
    }

    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "r2c-risk-overlay-"
            + now.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": (
            main_snapshot.get("snapshot_id")
        ),
        "method": METHOD,
        "fund_count": len(rows),
        "summary": summary,
        "holdings_refresh_errors": (
            holdings_errors
        ),
        "distribution_refresh_errors": (
            distribution_errors
        ),
        "rows": rows,
    }
    persist_snapshot(
        data_root,
        snapshot,
    )
    return snapshot


def persist_snapshot(
    data_root: str | Path,
    snapshot: dict,
) -> Path:
    root = Path(data_root)
    archive_dir = (
        root / "r2c_risk_overlay_snapshots"
    )
    archive_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    root.mkdir(
        parents=True,
        exist_ok=True,
    )
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
    ) + "\n"

    latest = (
        root / "r2c_risk_overlay_shadow.json"
    )
    archive = (
        archive_dir
        / f"{snapshot['snapshot_id']}.json"
    )
    for path in (archive, latest):
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(
                fd,
                "w",
                encoding="utf-8",
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(
                temp_name,
                path,
            )
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
    return archive


def run_loop(
    *,
    data_root: str | Path,
    timeout: int = 6,
    max_quote_age_seconds: int = 120,
    low_frequency_refresh_seconds: int = 21600,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    main_store = LofSnapshotStore(
        data_root
    )
    registry = load_registry()

    while True:
        started = time.monotonic()
        now = datetime.now(
            SHANGHAI_TZ
        )
        interval = off_hours_interval_seconds

        try:
            (
                _holdings,
                _schedules,
                holdings_errors,
                distribution_errors,
            ) = _load_or_refresh_low_frequency(
                data_root=data_root,
                registry=registry,
                now=now,
                timeout=timeout,
                refresh_seconds=(
                    low_frequency_refresh_seconds
                ),
            )

            main_snapshot = (
                main_store.load_latest()
            )
            if (
                main_snapshot is None
                or not _market_is_fresh(
                    main_snapshot
                )
            ):
                print(
                    json.dumps(
                        {
                            "event": (
                                "r2c_risk_overlay_idle"
                            ),
                            "time": now.isoformat(),
                            "reason": (
                                "MAIN_MARKET_NOT_FRESH"
                            ),
                            "holdings_refresh_errors": (
                                holdings_errors
                            ),
                            "distribution_refresh_errors": (
                                distribution_errors
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                snapshot = collect_once(
                    data_root=data_root,
                    now=now,
                    timeout=timeout,
                    max_quote_age_seconds=(
                        max_quote_age_seconds
                    ),
                    low_frequency_refresh_seconds=(
                        low_frequency_refresh_seconds
                    ),
                )
                interval = quote_interval_seconds
                print(
                    json.dumps(
                        {
                            "event": (
                                "r2c_risk_overlay_persisted"
                            ),
                            "time": now.isoformat(),
                            "snapshot_id": snapshot[
                                "snapshot_id"
                            ],
                            "summary": snapshot[
                                "summary"
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "event": (
                            "r2c_risk_overlay_failed"
                        ),
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

        elapsed = (
            time.monotonic() - started
        )
        time.sleep(
            max(
                0.0,
                interval - elapsed,
            )
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--data-root",
        required=True,
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=6,
    )
    parser.add_argument(
        "--max-quote-age-seconds",
        type=int,
        default=120,
    )
    parser.add_argument(
        "--low-frequency-refresh-seconds",
        type=int,
        default=21600,
    )
    parser.add_argument(
        "--quote-interval-seconds",
        type=float,
        default=30.0,
    )
    parser.add_argument(
        "--off-hours-interval-seconds",
        type=float,
        default=300.0,
    )
    parser.add_argument(
        "--loop",
        action="store_true",
    )
    return parser


def main(
    argv: list[str] | None = None,
) -> int:
    args = _parser().parse_args(argv)
    if args.loop:
        return run_loop(
            data_root=args.data_root,
            timeout=args.timeout,
            max_quote_age_seconds=(
                args.max_quote_age_seconds
            ),
            low_frequency_refresh_seconds=(
                args.low_frequency_refresh_seconds
            ),
            quote_interval_seconds=(
                args.quote_interval_seconds
            ),
            off_hours_interval_seconds=(
                args.off_hours_interval_seconds
            ),
        )

    snapshot = collect_once(
        data_root=args.data_root,
        timeout=args.timeout,
        max_quote_age_seconds=(
            args.max_quote_age_seconds
        ),
        low_frequency_refresh_seconds=(
            args.low_frequency_refresh_seconds
        ),
    )
    print(
        json.dumps(
            {
                "snapshot_id": snapshot[
                    "snapshot_id"
                ],
                "summary": snapshot[
                    "summary"
                ],
                "holdings_refresh_errors": (
                    snapshot[
                        "holdings_refresh_errors"
                    ]
                ),
                "distribution_refresh_errors": (
                    snapshot[
                        "distribution_refresh_errors"
                    ]
                ),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
