"""501300 USD-bond shadow-only sampler; never feeds main estimated NAV."""

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

from .r4_usd_bond_shadow import resolve_501300_usd_bond_shadow
from .snapshot_store import LofSnapshotStore
from .wscn_market_proxy import fetch_wscn_market_proxy


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FUND_CODE = "501300"
FUND_NAME = "美元债LOF"
CONTRACT_VERSION = "R4_USD_BOND_501300_SHADOW_V0"


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return value if value > 0 else None


def _market_row(snapshot: dict) -> dict | None:
    return next(
        (
            row
            for row in (snapshot.get("rows") or [])
            if str(row.get("code") or "") == FUND_CODE
        ),
        None,
    )


def _unavailable_row(
    *,
    market_row: dict | None,
    error: str,
) -> dict:
    return {
        "fund_code": FUND_CODE,
        "fund_name": (market_row or {}).get("name") or FUND_NAME,
        "method": "R4_USD_BOND_US10Y_FX_SHADOW_V0",
        "status": "UNAVAILABLE",
        "quality": "UNKNOWN",
        "shadow_estimated_nav": None,
        "shadow_premium_rate": None,
        "official_nav": (market_row or {}).get("official_nav"),
        "official_nav_date": (market_row or {}).get("official_nav_date"),
        "official_nav_lag_label": (market_row or {}).get("official_nav_lag_label"),
        "market_price": (market_row or {}).get("price"),
        "market_quote_status": (market_row or {}).get("quote_status"),
        "market_quote_time": (market_row or {}).get("quote_time"),
        "us10y_current": None,
        "us10y_previous_close": None,
        "us10y_time": None,
        "yield_change_pp": None,
        "bond_return": None,
        "usdcny_current": None,
        "usdcny_previous_close": None,
        "usdcny_time": None,
        "fx_return": None,
        "max_input_age_seconds": None,
        "eligible_for_main": False,
        "error": error,
    }


def calculate_shadow_row(
    *,
    main_snapshot: dict,
    us10y,
    usdcny,
    as_of: datetime,
    max_input_age_seconds: int = 1800,
) -> dict:
    market_row = _market_row(main_snapshot)
    if market_row is None:
        return _unavailable_row(
            market_row=None,
            error="FUND_NOT_IN_MAIN_SNAPSHOT",
        )

    official_nav = _decimal(market_row.get("official_nav"))
    if official_nav is None:
        return _unavailable_row(
            market_row=market_row,
            error="OFFICIAL_NAV_UNAVAILABLE",
        )

    result = resolve_501300_usd_bond_shadow(
        official_nav=official_nav,
        official_nav_lag_label=market_row.get("official_nav_lag_label"),
        us10y=us10y,
        usdcny=usdcny,
        as_of=as_of,
        max_input_age_seconds=max_input_age_seconds,
    )

    market_price = _decimal(market_row.get("price"))
    premium = None
    if (
        result.shadow_status == "AVAILABLE"
        and result.estimated_nav is not None
        and market_price is not None
        and market_row.get("quote_status") == "FRESH"
    ):
        premium = (
            market_price / result.estimated_nav
            - Decimal("1")
        ) * Decimal("100")

    return {
        "fund_code": FUND_CODE,
        "fund_name": market_row.get("name") or FUND_NAME,
        "method": result.resolver_method,
        "status": result.shadow_status,
        "quality": result.shadow_quality,
        "shadow_estimated_nav": (
            float(result.estimated_nav)
            if result.estimated_nav is not None
            else None
        ),
        "shadow_premium_rate": (
            float(premium)
            if premium is not None
            else None
        ),
        "official_nav": float(official_nav),
        "official_nav_date": market_row.get("official_nav_date"),
        "official_nav_lag_label": market_row.get("official_nav_lag_label"),
        "market_price": (
            float(market_price)
            if market_price is not None
            else None
        ),
        "market_quote_status": market_row.get("quote_status"),
        "market_quote_time": market_row.get("quote_time"),
        "us10y_current": float(us10y.current) if us10y.current is not None else None,
        "us10y_previous_close": (
            float(us10y.previous_close)
            if us10y.previous_close is not None
            else None
        ),
        "us10y_time": (
            us10y.quote_time.isoformat()
            if us10y.quote_time is not None
            else None
        ),
        "yield_change_pp": (
            float(result.yield_change_pp)
            if result.yield_change_pp is not None
            else None
        ),
        "bond_return": (
            float(result.bond_return)
            if result.bond_return is not None
            else None
        ),
        "usdcny_current": (
            float(usdcny.current)
            if usdcny.current is not None
            else None
        ),
        "usdcny_previous_close": (
            float(usdcny.previous_close)
            if usdcny.previous_close is not None
            else None
        ),
        "usdcny_time": (
            usdcny.quote_time.isoformat()
            if usdcny.quote_time is not None
            else None
        ),
        "fx_return": (
            float(result.fx_return)
            if result.fx_return is not None
            else None
        ),
        "max_input_age_seconds": result.max_input_age_seconds,
        "eligible_for_main": False,
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


def persist_snapshot(state_root: str | Path, snapshot: dict) -> Path:
    root = Path(state_root)
    archive = root / "snapshots" / f"{snapshot['snapshot_id']}.json"
    latest = root / "r4_usd_bond_501300_shadow.json"
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
    max_input_age_seconds: int = 1800,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(main_data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    us10y = fetch_wscn_market_proxy(
        "US10YR.OTC",
        timeout=timeout,
    )
    usdcny = fetch_wscn_market_proxy(
        "USDCNY.OTC",
        timeout=timeout,
    )
    row = calculate_shadow_row(
        main_snapshot=main_snapshot,
        us10y=us10y,
        usdcny=usdcny,
        as_of=now,
        max_input_age_seconds=max_input_age_seconds,
    )
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "r4-usd-bond-501300-shadow-"
            + now.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "method": "R4_USD_BOND_US10Y_FX_SHADOW_V0",
        "summary": {
            "available_count": int(row["status"] == "AVAILABLE"),
            "stale_count": int(row["status"] == "STALE"),
            "unavailable_count": int(row["status"] == "UNAVAILABLE"),
        },
        "rows": [row],
    }
    persist_snapshot(state_root, snapshot)
    return snapshot


def _active_sampling(main_snapshot: dict | None) -> bool:
    if main_snapshot is None:
        return False
    row = _market_row(main_snapshot)
    return bool(row and row.get("quote_status") == "FRESH")


def run_loop(
    *,
    main_data_root: str | Path,
    state_root: str | Path,
    timeout: int = 6,
    max_input_age_seconds: int = 1800,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    main_store = LofSnapshotStore(main_data_root)
    while True:
        started = time.monotonic()
        now = datetime.now(SHANGHAI_TZ)
        active = _active_sampling(main_store.load_latest())
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
                max_input_age_seconds=max_input_age_seconds,
            )
            print(
                json.dumps(
                    {
                        "event": "r4_usd_bond_shadow_persisted",
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
                        "event": "r4_usd_bond_shadow_failed",
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
    parser.add_argument(
        "--max-input-age-seconds",
        type=int,
        default=1800,
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
    parser.add_argument("--loop", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.loop:
        return run_loop(
            main_data_root=args.main_data_root,
            state_root=args.state_root,
            timeout=args.timeout,
            max_input_age_seconds=args.max_input_age_seconds,
            quote_interval_seconds=args.quote_interval_seconds,
            off_hours_interval_seconds=args.off_hours_interval_seconds,
        )

    snapshot = collect_once(
        main_data_root=args.main_data_root,
        state_root=args.state_root,
        timeout=args.timeout,
        max_input_age_seconds=args.max_input_age_seconds,
    )
    print(json.dumps(snapshot, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
