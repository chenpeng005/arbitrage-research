#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.lof.r2_promotion import (
    PROMOTED_R2_SOURCES,
    promoted_r2_method,
)
from runtime.lof.snapshot_archive import (
    iter_snapshot_paths,
    read_snapshot_json,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
VERSION = "LOF_R2_REOPEN_ACCEPTANCE_V1"


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed.astimezone(SHANGHAI_TZ)


def _snapshot_day(path: Path) -> date | None:
    name = path.name
    if not name.startswith("runtime-"):
        return None
    raw = name[len("runtime-"):len("runtime-") + 8]
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def promoted_codes() -> list[str]:
    codes: set[str] = set()
    for source in PROMOTED_R2_SOURCES.values():
        codes.update(str(code) for code in source["codes"])
    return sorted(codes)


def _market_is_fresh(snapshot: dict) -> bool:
    quality = snapshot.get("quality_summary") or {}
    if int(quality.get("quote_fresh_count") or 0) > 0:
        return True
    return any(
        row.get("quote_status") == "FRESH"
        for row in (snapshot.get("rows") or [])
        if isinstance(row, dict)
    )


def _row_by_code(snapshot: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in snapshot.get("rows") or []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "")
        if code:
            result[code] = row
    return result


def analyze_reopen(
    data_root: str | Path,
    *,
    day: date,
    expected_anchor_date: date,
) -> dict:
    paths = [
        path
        for path in iter_snapshot_paths(data_root)
        if _snapshot_day(path) == day
    ]
    paths = sorted(paths)

    codes = promoted_codes()
    first_market_fresh_at: datetime | None = None
    first_available_at: dict[str, datetime] = {}
    last_rows: dict[str, dict] = {}
    anchor_mismatch_codes: set[str] = set()
    snapshots_read = 0

    for path in paths:
        try:
            snapshot = read_snapshot_json(path)
        except Exception:
            continue

        generated_at = _parse_datetime(snapshot.get("generated_at"))
        if generated_at is None:
            continue

        snapshots_read += 1
        if first_market_fresh_at is None and _market_is_fresh(snapshot):
            first_market_fresh_at = generated_at

        rows = _row_by_code(snapshot)
        for code in codes:
            row = rows.get(code)
            if row is None:
                continue
            last_rows[code] = row

            method_info = promoted_r2_method(code)
            expected_method = method_info[1] if method_info else None
            anchor = str(row.get("official_nav_date") or "")
            if anchor and anchor != expected_anchor_date.isoformat():
                anchor_mismatch_codes.add(code)

            if code in first_available_at:
                continue

            if (
                row.get("estimated_nav_status") == "AVAILABLE"
                and row.get("estimated_nav_method") == expected_method
                and anchor == expected_anchor_date.isoformat()
            ):
                first_available_at[code] = generated_at

    per_fund: list[dict] = []
    lags: list[int] = []
    for code in codes:
        row = last_rows.get(code) or {}
        first_at = first_available_at.get(code)
        lag_seconds: int | None = None
        if first_at is not None and first_market_fresh_at is not None:
            lag_seconds = max(
                0,
                int((first_at - first_market_fresh_at).total_seconds()),
            )
            lags.append(lag_seconds)

        method_info = promoted_r2_method(code)
        per_fund.append(
            {
                "code": code,
                "name": row.get("name"),
                "source": method_info[0] if method_info else None,
                "expected_method": method_info[1] if method_info else None,
                "first_main_available_at": (
                    first_at.isoformat() if first_at else None
                ),
                "lag_from_main_market_fresh_seconds": lag_seconds,
                "last_status": row.get("estimated_nav_status"),
                "last_error": row.get("estimated_nav_error"),
                "last_official_nav_date": row.get("official_nav_date"),
                "last_estimated_nav_time": row.get("estimated_nav_time"),
            }
        )

    def _within(seconds: int) -> int:
        return sum(
            1
            for value in lags
            if value <= seconds
        )

    all_available_at = (
        max(first_available_at.values())
        if len(first_available_at) == len(codes)
        else None
    )

    return {
        "version": VERSION,
        "day": day.isoformat(),
        "expected_anchor_date": expected_anchor_date.isoformat(),
        "snapshot_files_found": len(paths),
        "snapshot_files_read": snapshots_read,
        "promoted_count": len(codes),
        "main_market_first_fresh_at": (
            first_market_fresh_at.isoformat()
            if first_market_fresh_at else None
        ),
        "available_count": len(first_available_at),
        "missing_available_codes": sorted(
            set(codes) - set(first_available_at)
        ),
        "available_within_60s": _within(60),
        "available_within_120s": _within(120),
        "available_within_300s": _within(300),
        "all_available_at": (
            all_available_at.isoformat()
            if all_available_at else None
        ),
        "max_lag_seconds": max(lags) if lags else None,
        "anchor_mismatch_codes": sorted(anchor_mismatch_codes),
        "status": (
            "PASS"
            if (
                first_market_fresh_at is not None
                and len(first_available_at) == len(codes)
                and not anchor_mismatch_codes
            )
            else "INCOMPLETE"
        ),
        "rows": per_fund,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze post-holiday R2 Main Promotion recovery from "
            "persisted LOF market snapshots. No network access."
        )
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--expected-anchor-date", required=True)
    args = parser.parse_args(argv)

    result = analyze_reopen(
        args.data_root,
        day=date.fromisoformat(args.day),
        expected_anchor_date=date.fromisoformat(
            args.expected_anchor_date
        ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
