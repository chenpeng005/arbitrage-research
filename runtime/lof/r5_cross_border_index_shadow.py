"""501025 cross-border direct-index Shadow.

The fund is currently classified R5_SPECIAL because it is a non-QDII
cross-border index fund. This Shadow deliberately does not change production
classification. It validates whether the official SSE-relayed RMB index
(sh000869) can support a high-confidence intraday NAV bridge.
"""

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

from .index_proxy import proxy_from_tracking_index_code
from .index_quote import IndexQuote
from .index_quote_xueqiu import fetch_index_quote_with_fallback
from .resolver import ResolverInput, resolve_estimated_nav
from .snapshot_store import LofSnapshotStore
from .trading_calendar import fetch_previous_trading_day


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
FUND_CODE = "501025"
FUND_NAME = "香港银行LOF"
METHOD = "R5_CROSS_BORDER_RELAY_INDEX_PREV_CLOSE"
CONTRACT_VERSION = "R5_CROSS_BORDER_INDEX_SHADOW_V0"
PROFILE_PATH = (
    Path(__file__).resolve().parent
    / "data"
    / "r5_cross_border_501025_profile_v0.json"
)


def _load_profile() -> dict:
    value = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    value["exposure_ratio"] = Decimal(str(value["exposure_ratio"]))
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


def _date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
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
    quote: IndexQuote | None,
    expected_anchor_date: date | None,
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
        "official_nav": (row or {}).get("official_nav"),
        "official_nav_date": (row or {}).get("official_nav_date"),
        "expected_anchor_date": (
            expected_anchor_date.isoformat()
            if expected_anchor_date is not None
            else None
        ),
        "market_price": (row or {}).get("price"),
        "market_quote_status": (row or {}).get("quote_status"),
        "proxy_id": PROFILE["index_code"],
        "proxy_symbol": PROFILE["tencent_symbol"],
        "proxy_source": quote.source if quote is not None else None,
        "proxy_current": (
            float(quote.current)
            if quote is not None and quote.current is not None
            else None
        ),
        "proxy_previous_close": (
            float(quote.previous_close)
            if quote is not None and quote.previous_close is not None
            else None
        ),
        "proxy_time": (
            quote.quote_time.isoformat()
            if quote is not None and quote.quote_time is not None
            else None
        ),
        "proxy_return": None,
        "exposure_ratio": float(PROFILE["exposure_ratio"]),
        "backtest": PROFILE["backtest"],
        "error": error,
    }


def calculate_shadow_row(
    *,
    main_snapshot: dict,
    quote: IndexQuote,
    expected_anchor_date: date,
    as_of: datetime,
    max_quote_age_seconds: int = 180,
) -> dict:
    row = _main_row(main_snapshot)
    if row is None:
        return _unavailable(
            row=None,
            quote=quote,
            expected_anchor_date=expected_anchor_date,
            error="FUND_NOT_IN_MAIN_SNAPSHOT",
        )

    official_nav = _decimal(row.get("official_nav"))
    official_nav_date = _date(row.get("official_nav_date"))
    if official_nav is None or official_nav_date is None:
        return _unavailable(
            row=row,
            quote=quote,
            expected_anchor_date=expected_anchor_date,
            error="OFFICIAL_NAV_UNAVAILABLE",
        )
    if official_nav_date != expected_anchor_date:
        return _unavailable(
            row=row,
            quote=quote,
            expected_anchor_date=expected_anchor_date,
            error="OFFICIAL_NAV_NOT_PREVIOUS_TRADING_DAY",
        )
    if quote.error is not None:
        return _unavailable(
            row=row,
            quote=quote,
            expected_anchor_date=expected_anchor_date,
            error=f"INDEX_QUOTE_ERROR:{quote.error}",
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
            proxy_id=PROFILE["index_code"],
            official_nav=official_nav,
            proxy_anchor_value=quote.previous_close,
            proxy_current_value=quote.current,
            proxy_anchor_time=anchor_time,
            proxy_current_time=quote.quote_time,
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
        "fund_name": row.get("name") or FUND_NAME,
        "method": METHOD,
        "status": result.estimated_nav_status,
        "quality_candidate": PROFILE["quality_candidate"],
        "research_group": PROFILE["research_group"],
        "economic_priority": PROFILE["economic_priority"],
        "eligible_for_main": False,
        "shadow_estimated_nav": (
            float(result.estimated_nav)
            if result.estimated_nav is not None
            else None
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
        "proxy_id": PROFILE["index_code"],
        "proxy_symbol": PROFILE["tencent_symbol"],
        "proxy_source": quote.source,
        "proxy_current": (
            float(quote.current) if quote.current is not None else None
        ),
        "proxy_previous_close": (
            float(quote.previous_close)
            if quote.previous_close is not None
            else None
        ),
        "proxy_time": (
            quote.quote_time.isoformat()
            if quote.quote_time is not None
            else None
        ),
        "proxy_return": (
            float(result.proxy_return)
            if result.proxy_return is not None
            else None
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


def persist_shadow(
    state_root: str | Path,
    snapshot: dict,
) -> Path:
    root = Path(state_root)
    archive = root / "snapshots" / f"{snapshot['snapshot_id']}.json"
    latest = root / "r5_cross_border_501025_shadow.json"
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
    mapping = proxy_from_tracking_index_code(
        tracking_target_name=PROFILE["tracking_target"],
        index_code=PROFILE["index_code"],
        index_name="HK银行",
    )
    quote = fetch_index_quote_with_fallback(
        tencent_symbol=mapping.tencent_symbol,
        xueqiu_symbol=mapping.xueqiu_symbol,
        timeout=timeout,
    )
    row = calculate_shadow_row(
        main_snapshot=main_snapshot,
        quote=quote,
        expected_anchor_date=expected_anchor_date,
        as_of=now,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "r5-cross-border-501025-shadow-"
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
                        "event": "r5_cross_border_shadow_persisted",
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
                        "event": "r5_cross_border_shadow_failed",
                        "time": now.isoformat(),
                        "error": (
                            f"{type(exc).__name__}:{str(exc)[:240]}"
                        ),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
        elapsed = time_module.monotonic() - started
        time_module.sleep(max(0.0, interval - elapsed))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-data-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--timeout", type=int, default=6)
    parser.add_argument("--max-quote-age-seconds", type=int, default=180)
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
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
