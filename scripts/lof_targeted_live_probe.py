#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.lof.fx import fetch_tencent_fx_daily, fetch_tencent_fx_quote, fx_close_on
from runtime.lof.fx_resolver import resolve_usdcny_input

TZ = ZoneInfo("Asia/Shanghai")
VERSION = "LOF_TARGETED_LIVE_PROBE_V1"


def infer_nav_date(data_root: str | Path) -> date:
    payload = json.loads(
        (Path(data_root) / "latest_market_snapshot.json").read_text(
            encoding="utf-8"
        )
    )
    values = []
    for row in payload.get("rows") or []:
        method = str(row.get("estimated_nav_method") or "")
        if "FX_BRIDGE" not in method and "CNH_FALLBACK_BRIDGE" not in method:
            continue
        raw = row.get("official_nav_date")
        if raw:
            values.append(str(raw))
    if not values:
        raise ValueError("no FX-dependent NAV date in latest snapshot")
    value, _ = Counter(values).most_common(1)[0]
    return date.fromisoformat(value)


def probe_fx(nav_date: date, timeout: int = 8) -> dict:
    now = datetime.now(TZ)
    usd = resolve_usdcny_input(
        nav_date=nav_date,
        as_of=now,
        timeout=timeout,
        max_quote_age_seconds=180,
    )
    usd_ok = (
        usd.anchor is not None
        and usd.current is not None
        and usd.quote_time is not None
        and usd.error is None
        and usd.status in {"AVAILABLE", "STALE"}
    )

    hkd_rows = fetch_tencent_fx_daily("whHKDCNY", count=30, timeout=timeout)
    hkd_anchor = fx_close_on(hkd_rows, nav_date)
    hkd = fetch_tencent_fx_quote("whHKDCNY", timeout=timeout)
    hkd_ok = (
        hkd_anchor is not None
        and hkd.current is not None
        and hkd.quote_time is not None
        and hkd.error is None
    )

    checks = [
        {
            "check": "USD_CNY_RESOLVER",
            "ok": usd_ok,
            "status": usd.status,
            "source": usd.source,
            "quote_time": (
                usd.quote_time.isoformat() if usd.quote_time else None
            ),
            "error": usd.error,
            "calibration_status": (
                usd.calibration.status if usd.calibration else None
            ),
        },
        {
            "check": "HKD_CNY_DIRECT",
            "ok": hkd_ok,
            "source": hkd.source,
            "quote_time": (
                hkd.quote_time.isoformat() if hkd.quote_time else None
            ),
            "error": hkd.error,
        },
    ]
    return {
        "version": VERSION,
        "domain": "fx",
        "nav_date": nav_date.isoformat(),
        "as_of": now.isoformat(),
        "status": "PASS" if all(x["ok"] for x in checks) else "FAIL",
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", choices=["fx"], required=True)
    parser.add_argument("--data-root")
    parser.add_argument("--nav-date")
    parser.add_argument("--timeout", type=int, default=8)
    args = parser.parse_args()

    nav_date = (
        date.fromisoformat(args.nav_date)
        if args.nav_date
        else infer_nav_date(args.data_root)
    )
    result = probe_fx(nav_date, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
