from __future__ import annotations

import argparse
from datetime import datetime, time as clock_time
import json
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .nav import OfficialNavRecord, fetch_all_official_nav
from .runtime_session import LofRuntimeSession
from .snapshot_store import LofSnapshotStore
from .state import FundTradeStateRecord, fetch_all_trade_states


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def is_market_refresh_window(now: datetime) -> bool:
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI_TZ)
    local = now.astimezone(SHANGHAI_TZ)
    if local.weekday() >= 5:
        return False
    value = local.time()
    return (
        clock_time(9, 25) <= value <= clock_time(11, 35)
        or clock_time(12, 55) <= value <= clock_time(15, 5)
    )


def _log(event: str, **fields) -> None:
    payload = {
        "event": event,
        "time": datetime.now(SHANGHAI_TZ).isoformat(),
        **fields,
    }
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def _refresh_nav(
    session: LofRuntimeSession,
    *,
    timeout: int,
    as_of: datetime,
    previous_records: list[OfficialNavRecord] | None = None,
) -> list[OfficialNavRecord]:
    return fetch_all_official_nav(
        session.universe,
        timeout=timeout,
        szse_relay_bundle=session.szse_relay_bundle,
        expected_nav_date=(
            session.estimated_nav_context.previous_trading_day
        ),
        fallback_as_of_date=as_of.date(),
        previous_records=previous_records,
    )


def _nav_refresh_fields(
    rows: list[OfficialNavRecord],
    *,
    expected_nav_date,
) -> dict:
    available = [row for row in rows if row.available]
    dates = [row.nav_date for row in available if row.nav_date is not None]
    return {
        "count": len(rows),
        "available_count": len(available),
        "expected_nav_date": (
            expected_nav_date.isoformat()
            if expected_nav_date is not None
            else None
        ),
        "latest_nav_date": (
            max(dates).isoformat() if dates else None
        ),
        "expected_or_newer_count": sum(
            row.nav_date is not None
            and expected_nav_date is not None
            and row.nav_date >= expected_nav_date
            for row in available
        ),
        "older_than_expected_count": sum(
            row.nav_date is not None
            and expected_nav_date is not None
            and row.nav_date < expected_nav_date
            for row in available
        ),
        "published_fallback_count": sum(
            row.source == "EASTMONEY_PUBLISHED_NAV_FALLBACK"
            for row in available
        ),
    }


def _refresh_state(
    session: LofRuntimeSession,
    *,
    timeout: int,
) -> list[FundTradeStateRecord]:
    return fetch_all_trade_states(
        session.universe,
        timeout=timeout,
    )


def run_runtime_loop(
    *,
    data_root: str | Path,
    quote_interval_seconds: float = 30.0,
    off_hours_interval_seconds: float = 300.0,
    state_refresh_seconds: float = 600.0,
    nav_refresh_seconds: float = 1800.0,
    session_refresh_seconds: float = 3600.0,
    timeout: int = 30,
    max_quote_age_seconds: int = 90,
    max_cycles: int | None = None,
    now_fn: Callable[[], datetime] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> int:
    now_fn = now_fn or (lambda: datetime.now(SHANGHAI_TZ))
    store = LofSnapshotStore(data_root)
    stop_event = threading.Event()

    def request_stop(signum, frame) -> None:
        stop_event.set()
        _log("stop_requested", signal=signum)

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGTERM, request_stop)
        signal.signal(signal.SIGINT, request_stop)

    now = now_fn()
    session = LofRuntimeSession.build(as_of=now, timeout=timeout)
    nav_cache = _refresh_nav(
        session,
        timeout=timeout,
        as_of=now,
    )
    state_cache = _refresh_state(session, timeout=timeout)
    last_session_refresh = monotonic_fn()
    last_nav_refresh = last_session_refresh
    last_state_refresh = last_session_refresh

    _log(
        "runtime_started",
        universe_count=len(session.universe),
        r1_context_resolved=session.context_build.r1_resolved_count,
        r1_context_unresolved=session.context_build.r1_unresolved_count,
        szse_transport=session.szse_transport,
    )

    cycle = 0
    while not stop_event.is_set():
        cycle_started = monotonic_fn()
        now = now_fn()

        if cycle_started - last_session_refresh >= session_refresh_seconds:
            try:
                new_session = LofRuntimeSession.build(
                    as_of=now,
                    timeout=timeout,
                )
                new_nav = _refresh_nav(
                    new_session,
                    timeout=timeout,
                    as_of=now,
                    previous_records=nav_cache,
                )
                new_state = _refresh_state(new_session, timeout=timeout)
                session = new_session
                nav_cache = new_nav
                state_cache = new_state
                last_session_refresh = cycle_started
                last_nav_refresh = cycle_started
                last_state_refresh = cycle_started
                _log(
                    "session_refreshed",
                    universe_count=len(session.universe),
                    szse_transport=session.szse_transport,
                )
            except Exception as exc:
                _log(
                    "session_refresh_failed",
                    error=f"{type(exc).__name__}:{exc}",
                )

        if cycle_started - last_nav_refresh >= nav_refresh_seconds:
            try:
                nav_cache = _refresh_nav(
                    session,
                    timeout=timeout,
                    as_of=now,
                    previous_records=nav_cache,
                )
                last_nav_refresh = cycle_started
                _log(
                    "nav_refreshed",
                    **_nav_refresh_fields(
                        nav_cache,
                        expected_nav_date=(
                            session.estimated_nav_context.previous_trading_day
                        ),
                    ),
                )
            except Exception as exc:
                _log(
                    "nav_refresh_failed",
                    error=f"{type(exc).__name__}:{exc}",
                )

        if cycle_started - last_state_refresh >= state_refresh_seconds:
            try:
                state_cache = _refresh_state(session, timeout=timeout)
                last_state_refresh = cycle_started
                _log("state_refreshed", count=len(state_cache))
            except Exception as exc:
                _log(
                    "state_refresh_failed",
                    error=f"{type(exc).__name__}:{exc}",
                )

        snapshot_id = "runtime-" + now.strftime("%Y%m%dT%H%M%S")
        try:
            snapshot = session.collect(
                generated_at=now,
                market_cutoff=now,
                max_quote_age_seconds=max_quote_age_seconds,
                timeout=timeout,
                snapshot_id=snapshot_id,
                official_nav_override=nav_cache,
                trade_states_override=state_cache,
            )
            path = store.persist(snapshot)
            quality = snapshot.get("quality_summary") or {}
            _log(
                "snapshot_persisted",
                snapshot_id=snapshot.get("snapshot_id"),
                collector_status=snapshot.get("collector_status"),
                universe_count=snapshot.get("universe_count"),
                quote_fresh_count=quality.get("quote_fresh_count"),
                estimated_nav_available_count=quality.get(
                    "estimated_nav_available_count"
                ),
                path=str(path),
            )
        except Exception as exc:
            _log(
                "snapshot_failed",
                error=f"{type(exc).__name__}:{exc}",
            )

        cycle += 1
        if max_cycles is not None and cycle >= max_cycles:
            break

        interval = (
            quote_interval_seconds
            if is_market_refresh_window(now)
            else off_hours_interval_seconds
        )
        elapsed = monotonic_fn() - cycle_started
        remaining = max(0.0, interval - elapsed)
        if remaining > 0:
            sleep_fn(remaining)

    _log("runtime_stopped", cycles=cycle)
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run long-lived LOF snapshot refresh loop."
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--quote-interval-seconds", type=float, default=30.0)
    parser.add_argument("--off-hours-interval-seconds", type=float, default=300.0)
    parser.add_argument("--state-refresh-seconds", type=float, default=600.0)
    parser.add_argument("--nav-refresh-seconds", type=float, default=1800.0)
    parser.add_argument("--session-refresh-seconds", type=float, default=3600.0)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--max-quote-age-seconds", type=int, default=90)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return run_runtime_loop(
        data_root=args.data_root,
        quote_interval_seconds=args.quote_interval_seconds,
        off_hours_interval_seconds=args.off_hours_interval_seconds,
        state_refresh_seconds=args.state_refresh_seconds,
        nav_refresh_seconds=args.nav_refresh_seconds,
        session_refresh_seconds=args.session_refresh_seconds,
        timeout=args.timeout,
        max_quote_age_seconds=args.max_quote_age_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
