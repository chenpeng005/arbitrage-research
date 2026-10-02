"""Shared USD/CNY cross-source shadow for R3/R5; no production feed."""

from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from .fx import fetch_tencent_fx_daily, fetch_tencent_fx_quote
from .fx_cross_source import (
    build_usdcny_cross_source_bridge,
    calibrate_usdcny_sources,
    fetch_wscn_daily_fx,
)
from .snapshot_store import LofSnapshotStore
from .wscn_market_proxy import fetch_wscn_market_proxy


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
CONTRACT_VERSION = "USDCNY_CROSS_SOURCE_SHADOW_V0"


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


def collect_once(
    *,
    main_data_root: str | Path,
    state_root: str | Path,
    now: datetime | None = None,
    timeout: int = 8,
    history_count: int = 120,
    max_quote_age_seconds: int = 180,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    main_snapshot = LofSnapshotStore(main_data_root).load_latest()
    if main_snapshot is None:
        raise RuntimeError("MAIN_SNAPSHOT_UNAVAILABLE")

    tencent_rows = fetch_tencent_fx_daily(
        "whUSDCNY",
        count=max(history_count, 140),
        timeout=timeout,
    )
    wscn_rows = fetch_wscn_daily_fx(
        count=history_count,
        timeout=timeout,
    )
    calibration = calibrate_usdcny_sources(
        tencent_rows=tencent_rows,
        wscn_rows=wscn_rows,
    )
    wscn_quote = fetch_wscn_market_proxy(
        "USDCNY.OTC",
        timeout=timeout,
    )
    tencent_quote = fetch_tencent_fx_quote(
        "whUSDCNY",
        timeout=timeout,
    )

    target_dates: set[date] = set()
    for row in main_snapshot.get("rows") or []:
        if row.get("resolver_class") not in {
            "R3_QDII_INDEX",
            "R5_SPECIAL",
        }:
            continue
        raw = row.get("official_nav_date")
        if not raw:
            continue
        try:
            target_dates.add(date.fromisoformat(str(raw)))
        except ValueError:
            continue

    rows = []
    for nav_date in sorted(target_dates):
        bridge = build_usdcny_cross_source_bridge(
            nav_date=nav_date,
            tencent_rows=tencent_rows,
            wscn_quote=wscn_quote,
            calibration=calibration,
            as_of=now,
            max_quote_age_seconds=max_quote_age_seconds,
        )
        rows.append(
            {
                "nav_date": nav_date.isoformat(),
                "status": bridge.status,
                "anchor": (
                    float(bridge.anchor)
                    if bridge.anchor is not None else None
                ),
                "current": (
                    float(bridge.current)
                    if bridge.current is not None else None
                ),
                "fx_return": (
                    float(bridge.fx_return)
                    if bridge.fx_return is not None else None
                ),
                "quote_time": (
                    bridge.quote_time.isoformat()
                    if bridge.quote_time is not None else None
                ),
                "quote_age_seconds": bridge.quote_age_seconds,
                "source": bridge.source,
                "error": bridge.error,
            }
        )

    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": (
            "usdcny-cross-source-shadow-"
            + now.strftime("%Y%m%dT%H%M%S")
        ),
        "generated_at": now.isoformat(),
        "source_market_snapshot_id": main_snapshot.get("snapshot_id"),
        "eligible_for_main": False,
        "calibration": {
            "status": calibration.status,
            "common_count": calibration.common_count,
            "common_start": (
                calibration.common_start.isoformat()
                if calibration.common_start else None
            ),
            "common_end": (
                calibration.common_end.isoformat()
                if calibration.common_end else None
            ),
            "median_abs_level_diff_bps": (
                calibration.median_abs_level_diff_bps
            ),
            "p90_abs_level_diff_bps": (
                calibration.p90_abs_level_diff_bps
            ),
            "max_abs_level_diff_bps": (
                calibration.max_abs_level_diff_bps
            ),
            "error": calibration.error,
        },
        "wscn_current": {
            "value": (
                float(wscn_quote.current)
                if wscn_quote.current is not None else None
            ),
            "quote_time": (
                wscn_quote.quote_time.isoformat()
                if wscn_quote.quote_time is not None else None
            ),
            "error": wscn_quote.error,
        },
        "tencent_current": {
            "value": (
                float(tencent_quote.current)
                if tencent_quote.current is not None else None
            ),
            "quote_time": (
                tencent_quote.quote_time.isoformat()
                if tencent_quote.quote_time is not None else None
            ),
            "error": tencent_quote.error,
        },
        "rows": rows,
    }

    root = Path(state_root)
    payload = json.dumps(
        snapshot,
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    _atomic_write(root / "usdcny_cross_source_shadow.json", payload)
    _atomic_write(
        root / "snapshots" / f"{snapshot['snapshot_id']}.json",
        payload,
    )
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-data-root", required=True)
    parser.add_argument("--state-root", required=True)
    parser.add_argument("--timeout", type=int, default=8)
    parser.add_argument("--history-count", type=int, default=120)
    parser.add_argument("--max-quote-age-seconds", type=int, default=180)
    args = parser.parse_args(argv)

    snapshot = collect_once(
        main_data_root=args.main_data_root,
        state_root=args.state_root,
        timeout=args.timeout,
        history_count=args.history_count,
        max_quote_age_seconds=args.max_quote_age_seconds,
    )
    print(json.dumps(snapshot, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
