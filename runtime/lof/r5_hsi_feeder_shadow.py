"""501302 HSI feeder shadow; research-only and never feeds main NAV."""

from __future__ import annotations

import argparse
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import tempfile
import time as time_module
from zoneinfo import ZoneInfo

from .foreign_quote import fetch_tencent_foreign_quote
from .hk_history import fetch_tencent_hk_daily, hk_close_on
from .resolver import ResolverInput, resolve_estimated_nav
from .snapshot_store import LofSnapshotStore
from .trading_calendar import fetch_previous_trading_day


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
METHOD = "R5_HSI_FEEDER_DIRECT_INDEX"
CONTRACT_VERSION = "R5_HSI_FEEDER_SHADOW_V0"
PROFILE_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "r5_hsi_feeder_501302_profile_v0.json"
)


def _profile() -> dict:
    value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    value["exposure_ratio"] = Decimal(str(value["exposure_ratio"]))
    return value


PROFILE = _profile()
FUND_CODE = PROFILE["fund_code"]


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result > 0 else None


def _date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _main_row(snapshot: dict) -> dict | None:
    return next(
        (
            row
            for row in (snapshot.get("rows") or [])
            if str(row.get("code") or "") == FUND_CODE
        ),
        None,
    )


def _unavailable(
    *,
    row: dict | None,
    expected_anchor_date: date | None,
    error: str,
    proxy_time: datetime | None = None,
) -> dict:
    return {
        "fund_code": FUND_CODE,
        "fund_name": (row or {}).get("name") or PROFILE["name"],
        "method": METHOD,
        "status": "UNAVAILABLE",
        "quality_candidate": PROFILE["quality_candidate"],
        "research_group": "R5-CROSS-BORDER-INDEX",
        "economic_priority": PROFILE["economic_priority"],
        "eligible_for_main": False,
        "shadow_estimated_nav": None,
        "shadow_premium_rate": None,
        "official_nav": (row or {}).get("official_nav"),
        "official_nav_date": (row or {}).get("official_nav_date"),
        "expected_anchor_date": (
            expected_anchor_date.isoformat()
            if expected_anchor_date else None
        ),
        "market_price": (row or {}).get("price"),
        "market_quote_status": (row or {}).get("quote_status"),
        "proxy_id": "HSI",
        "proxy_symbol": PROFILE["proxy_symbol"],
        "proxy_time": (
            proxy_time.isoformat() if proxy_time else None
        ),
        "proxy_return": None,
        "exposure_ratio": float(PROFILE["exposure_ratio"]),
        "backtest": PROFILE["backtest"],
        "error": error,
    }


def calculate_shadow_row(
    *,
    main_snapshot: dict,
    proxy_anchor: Decimal | None,
    proxy_current: Decimal | None,
    proxy_time: datetime | None,
    proxy_error: str | None,
    expected_anchor_date: date,
    as_of: datetime,
    max_quote_age_seconds: int = 180,
) -> dict:
    row = _main_row(main_snapshot)
    if row is None:
        return _unavailable(
            row=None,
            expected_anchor_date=expected_anchor_date,
            error="FUND_NOT_IN_MAIN_SNAPSHOT",
            proxy_time=proxy_time,
        )

    official_nav = _decimal(row.get("official_nav"))
    official_nav_date = _date(row.get("official_nav_date"))
    if official_nav is None or official_nav_date is None:
        return _unavailable(
            row=row,
            expected_anchor_date=expected_anchor_date,
            error="OFFICIAL_NAV_UNAVAILABLE",
            proxy_time=proxy_time,
        )
    if official_nav_date != expected_anchor_date:
        return _unavailable(
            row=row,
            expected_anchor_date=expected_anchor_date,
            error="OFFICIAL_NAV_NOT_PREVIOUS_TRADING_DAY",
            proxy_time=proxy_time,
        )
    if proxy_error:
        return _unavailable(
            row=row,
            expected_anchor_date=expected_anchor_date,
            error=f"INDEX_QUOTE_ERROR:{proxy_error}",
            proxy_time=proxy_time,
        )
    if proxy_anchor is None or proxy_anchor <= 0:
        return _unavailable(
            row=row,
            expected_anchor_date=expected_anchor_date,
            error="HSI_ANCHOR_UNAVAILABLE",
            proxy_time=proxy_time,
        )

    anchor_time = datetime.combine(
        expected_anchor_date,
        time(16, 0),
        tzinfo=SHANGHAI_TZ,
    )
    result = resolve_estimated_nav(
        ResolverInput(
            fund_code=FUND_CODE,
            resolver_class="R5_SPECIAL",
            resolver_method=METHOD,
            proxy_id="HSI",
            official_nav=official_nav,
            proxy_anchor_value=proxy_anchor,
            proxy_current_value=proxy_current,
            proxy_anchor_time=anchor_time,
            proxy_current_time=proxy_time,
            as_of=as_of,
            max_proxy_age_seconds=max_quote_age_seconds,
            exposure_ratio=PROFILE["exposure_ratio"],
            quality=PROFILE["quality_candidate"],
        )
    )

    market_price = _decimal(row.get("price"))
    premium = None
    if (
        result.estimated_nav_status == "AVAILABLE"
        and result.estimated_nav is not None
        and row.get("quote_status") == "FRESH"
        and market_price is not None
    ):
        premium = (
            market_price / result.estimated_nav - Decimal("1")
        ) * Decimal("100")

    return {
        "fund_code": FUND_CODE,
        "fund_name": row.get("name") or PROFILE["name"],
        "method": METHOD,
        "status": result.estimated_nav_status,
        "quality_candidate": PROFILE["quality_candidate"],
        "research_group": "R5-CROSS-BORDER-INDEX",
        "economic_priority": PROFILE["economic_priority"],
        "eligible_for_main": False,
        "shadow_estimated_nav": (
            float(result.estimated_nav)
            if result.estimated_nav is not None else None
        ),
        "shadow_premium_rate": (
            float(premium) if premium is not None else None
        ),
        "official_nav": float(official_nav),
        "official_nav_date": official_nav_date.isoformat(),
        "expected_anchor_date": expected_anchor_date.isoformat(),
        "market_price": (
            float(market_price) if market_price is not None else None
        ),
        "market_quote_status": row.get("quote_status"),
        "proxy_id": "HSI",
        "proxy_symbol": PROFILE["proxy_symbol"],
        "proxy_time": (
            proxy_time.isoformat() if proxy_time else None
        ),
        "proxy_return": (
            float(result.proxy_return)
            if result.proxy_return is not None else None
        ),
        "exposure_ratio": float(PROFILE["exposure_ratio"]),
        "backtest": PROFILE["backtest"],
        "error": result.error,
    }


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


def persist_shadow(state_root: str | Path, snapshot: dict) -> Path:
    root = Path(state_root)
    archive = root / "snapshots" / f"{snapshot['snapshot_id']}.json"
    latest = root / "r5_hsi_feeder_501302_shadow.json"
    payload = json.dumps(
        snapshot, ensure_ascii=False, separators=(",", ":"), default=str
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
    max_quote_age_seconds: int = 180,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(main_data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    expected_anchor_date = fetch_previous_trading_day(
        as_of=now.date(),
        timeout=timeout,
    )
    history = fetch_tencent_hk_daily(
        symbol=PROFILE["proxy_symbol"],
        count=45,
        timeout=timeout,
    )
    anchor = hk_close_on(history, expected_anchor_date)
    quote = fetch_tencent_foreign_quote(
        PROFILE["proxy_symbol"],
        timeout=timeout,
    )
    row = calculate_shadow_row(
        main_snapshot=main_snapshot,
        proxy_anchor=anchor,
        proxy_current=quote.current,
        proxy_time=quote.quote_time,
        proxy_error=quote.error,
        expected_anchor_date=expected_anchor_date,
        as_of=now,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "r5-hsi-feeder-501302-shadow-"
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
    max_quote_age_seconds: int = 180,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    main_store = LofSnapshotStore(main_data_root)
    while True:
        started = time_module.monotonic()
        now = datetime.now(SHANGHAI_TZ)
        main_snapshot = main_store.load_latest()
        row = _main_row(main_snapshot or {})
        active = bool(row and row.get("quote_status") == "FRESH")
        interval = (
            quote_interval_seconds if active else off_hours_interval_seconds
        )
        try:
            snapshot = collect_once(
                main_data_root=main_data_root,
                state_root=state_root,
                now=now,
                timeout=timeout,
                max_quote_age_seconds=max_quote_age_seconds,
            )
            print(json.dumps({
                "event": "r5_hsi_feeder_shadow_persisted",
                "time": now.isoformat(),
                "snapshot_id": snapshot["snapshot_id"],
                "summary": snapshot["summary"],
                "active_sampling": active,
            }, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({
                "event": "r5_hsi_feeder_shadow_failed",
                "time": now.isoformat(),
                "error": f"{type(exc).__name__}:{str(exc)[:240]}",
            }, ensure_ascii=False), flush=True)
        elapsed = time_module.monotonic() - started
        time_module.sleep(max(0.0, interval - elapsed))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-data-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument("--max-quote-age-seconds", type=int, default=180)
    parser.add_argument("--quote-interval-seconds", type=float, default=30.0)
    parser.add_argument(
        "--off-hours-interval-seconds", type=float, default=300.0
    )
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args(argv)
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
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
