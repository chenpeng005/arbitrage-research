"""R4 India shadow-only sampler; never feeds main estimated NAV."""

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

from .india_index_quote import fetch_india_sensex_quote
from .r4_india_shadow import india_cash_timing_regime, resolve_164824_india_shadow
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FUND_CODE = "164824"
FUND_NAME = "印度基金LOF"
CONTRACT_VERSION = "R4_INDIA_TIMING_SHADOW_V0"


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result > 0 else None


def _market_row(main_snapshot: dict) -> dict | None:
    return next(
        (
            row
            for row in (main_snapshot.get("rows") or [])
            if str(row.get("code") or "") == FUND_CODE
        ),
        None,
    )


def _unavailable_row(
    *,
    market_row: dict | None,
    error: str,
    as_of: datetime,
) -> dict:
    return {
        "fund_code": FUND_CODE,
        "fund_name": (
            (market_row or {}).get("name")
            or FUND_NAME
        ),
        "method": "R4_INDIA_SENSEX_TIMING_SHADOW_V0",
        "status": "UNAVAILABLE",
        "quality": "UNKNOWN",
        "shadow_estimated_nav": None,
        "shadow_premium_rate": None,
        "official_nav": (market_row or {}).get("official_nav"),
        "official_nav_date": (market_row or {}).get("official_nav_date"),
        "market_price": (market_row or {}).get("price"),
        "market_quote_status": (market_row or {}).get("quote_status"),
        "market_quote_time": (market_row or {}).get("quote_time"),
        "proxy_id": None,
        "proxy_source": None,
        "proxy_time": None,
        "proxy_return": None,
        "proxy_quote_age_seconds": None,
        "timing_regime": india_cash_timing_regime(as_of),
        "eligible_for_main": False,
        "error": error,
    }


def calculate_shadow_row(
    *,
    main_snapshot: dict,
    quote,
    as_of: datetime,
    max_quote_age_seconds: int = 1800,
) -> dict:
    market_row = _market_row(main_snapshot)
    if market_row is None:
        return _unavailable_row(
            market_row=None,
            error="FUND_NOT_IN_MAIN_SNAPSHOT",
            as_of=as_of,
        )

    official_nav = _decimal(market_row.get("official_nav"))
    if official_nav is None:
        return _unavailable_row(
            market_row=market_row,
            error="OFFICIAL_NAV_UNAVAILABLE",
            as_of=as_of,
        )

    result = resolve_164824_india_shadow(
        official_nav=official_nav,
        quote=quote,
        as_of=as_of,
        max_quote_age_seconds=max_quote_age_seconds,
    )

    market_price = _decimal(market_row.get("price"))
    premium = None
    if (
        result.shadow_status == "AVAILABLE"
        and result.estimated_nav is not None
        and market_price is not None
        and str(market_row.get("quote_status") or "") == "FRESH"
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
        "market_price": (
            float(market_price)
            if market_price is not None
            else None
        ),
        "market_quote_status": market_row.get("quote_status"),
        "market_quote_time": market_row.get("quote_time"),
        "proxy_id": result.proxy_id,
        "proxy_source": quote.source,
        "proxy_time": (
            result.proxy_time.isoformat()
            if result.proxy_time is not None
            else None
        ),
        "proxy_return": (
            float(result.proxy_return)
            if result.proxy_return is not None
            else None
        ),
        "proxy_quote_age_seconds": result.quote_age_seconds,
        "timing_regime": result.timing_regime,
        "eligible_for_main": False,
        "error": result.error,
    }


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def persist_shadow_snapshot(
    state_root: str | Path,
    snapshot: dict,
) -> Path:
    root = Path(state_root)
    archive = root / "snapshots" / f"{snapshot['snapshot_id']}.json"
    latest = root / "r4_india_shadow.json"
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
    max_quote_age_seconds: int = 1800,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(main_data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    quote = fetch_india_sensex_quote(timeout=timeout)
    row = calculate_shadow_row(
        main_snapshot=main_snapshot,
        quote=quote,
        as_of=now,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "r4-india-shadow-"
            + now.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "method": "R4_INDIA_SENSEX_TIMING_SHADOW_V0",
        "summary": {
            "available_count": int(row["status"] == "AVAILABLE"),
            "stale_count": int(row["status"] == "STALE"),
            "unavailable_count": int(row["status"] == "UNAVAILABLE"),
        },
        "rows": [row],
    }
    persist_shadow_snapshot(state_root, snapshot)
    return snapshot


def _main_row_fresh(main_snapshot: dict | None) -> bool:
    if main_snapshot is None:
        return False
    row = _market_row(main_snapshot)
    return bool(row and row.get("quote_status") == "FRESH")


def run_loop(
    *,
    main_data_root: str | Path,
    state_root: str | Path,
    timeout: int = 6,
    max_quote_age_seconds: int = 1800,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
) -> int:
    main_store = LofSnapshotStore(main_data_root)
    while True:
        started = time.monotonic()
        now = datetime.now(SHANGHAI_TZ)
        main_snapshot = main_store.load_latest()
        active = (
            india_cash_timing_regime(now) == "INDIA_CASH_OPEN"
            and _main_row_fresh(main_snapshot)
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
                        "event": "r4_india_shadow_persisted",
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
                        "event": "r4_india_shadow_failed",
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
        "--max-quote-age-seconds",
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
                "source_market_snapshot_id": snapshot["source_market_snapshot_id"],
                "summary": snapshot["summary"],
                "row": snapshot["rows"][0],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
