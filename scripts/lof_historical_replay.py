#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from runtime.lof.resolver import ResolverInput, resolve_estimated_nav
from runtime.lof.snapshot_archive import iter_snapshot_paths, read_snapshot_json


REPLAY_VERSION = "LOF_HISTORICAL_FORMULA_REPLAY_V1"
EXCLUDED_METHODS = {
    "UNAVAILABLE",
    "DISCLOSED_HOLDINGS_BASKET",
    "R2B2_CASH_HEAVY_HOLDINGS_BASKET",
    "RISK_ASSET_OVERLAY",
}


def _decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(str(value))


def _snapshot_day(path: Path) -> str | None:
    name = path.name
    if not name.startswith("runtime-"):
        return None
    raw = name[len("runtime-"):len("runtime-") + 8]
    try:
        return datetime.strptime(raw, "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def _eligible(row: dict) -> bool:
    method = str(row.get("estimated_nav_method") or "")
    if not method or method in EXCLUDED_METHODS:
        return False
    return (
        row.get("estimated_nav") is not None
        and row.get("official_nav") is not None
        and row.get("estimated_nav_proxy_return") is not None
        and row.get("estimated_nav_proxy_time") is not None
    )


def _replay_row(row: dict) -> tuple[Decimal | None, str | None]:
    official_nav = _decimal(row.get("official_nav"))
    proxy_return = _decimal(row.get("estimated_nav_proxy_return"))
    fx_return = _decimal(row.get("estimated_nav_fx_return"))
    exposure = _decimal(row.get("estimated_nav_exposure_ratio"))
    tracking = (
        _decimal(row.get("estimated_nav_tracking_adjustment"))
        or Decimal("1")
    )
    proxy_time = _datetime(row.get("estimated_nav_proxy_time"))

    if (
        official_nav is None
        or proxy_return is None
        or proxy_time is None
    ):
        return None, "MISSING_REPLAY_INPUT"

    fx_anchor = None
    fx_current = None
    if fx_return is not None:
        fx_anchor = Decimal("1")
        fx_current = Decimal("1") + fx_return

    result = resolve_estimated_nav(
        ResolverInput(
            fund_code=str(row.get("code") or ""),
            resolver_class=str(row.get("resolver_class") or "UNKNOWN"),
            resolver_method=str(
                row.get("estimated_nav_method") or "UNKNOWN"
            ),
            proxy_id=str(row.get("estimated_nav_proxy") or "REPLAY"),
            official_nav=official_nav,
            proxy_anchor_value=Decimal("1"),
            proxy_current_value=Decimal("1") + proxy_return,
            proxy_anchor_time=proxy_time - timedelta(days=1),
            proxy_current_time=proxy_time,
            # Replay formula under a fresh clock so historical wall-clock
            # staleness does not affect the arithmetic comparison.
            as_of=proxy_time + timedelta(seconds=1),
            max_proxy_age_seconds=120,
            exposure_ratio=exposure,
            fx_anchor=fx_anchor,
            fx_current=fx_current,
            tracking_adjustment=tracking,
            quality=str(row.get("estimated_nav_quality") or "UNKNOWN"),
            fx_current_time=_datetime(row.get("estimated_nav_fx_time")),
            fx_source=row.get("estimated_nav_fx_source"),
        )
    )
    return result.estimated_nav, result.error


def run_replay(
    data_root: str | Path,
    *,
    day: str,
    sample_every: int = 1,
    resolver_classes: set[str] | None = None,
    codes: set[str] | None = None,
    tolerance: Decimal = Decimal("0.000000000001"),
) -> dict:
    paths = [
        path
        for path in iter_snapshot_paths(data_root)
        if _snapshot_day(path) == day
    ]
    paths = sorted(paths)
    if sample_every > 1:
        paths = paths[::sample_every]

    rows_checked = 0
    failures: list[dict] = []
    method_counts: Counter[str] = Counter()
    code_counts: Counter[str] = Counter()

    for path in paths:
        snapshot = read_snapshot_json(path)
        for row in snapshot.get("rows") or []:
            code = str(row.get("code") or "")
            resolver_class = str(row.get("resolver_class") or "")
            if resolver_classes and resolver_class not in resolver_classes:
                continue
            if codes and code not in codes:
                continue
            if not _eligible(row):
                continue

            expected = _decimal(row.get("estimated_nav"))
            actual, error = _replay_row(row)
            rows_checked += 1
            method_counts[str(row.get("estimated_nav_method"))] += 1
            code_counts[code] += 1

            reasons: list[str] = []
            if error:
                reasons.append(f"resolver_error={error}")
            if actual is None:
                reasons.append("replayed_nav=None")
            elif expected is None:
                reasons.append("stored_nav=None")
            elif abs(actual - expected) > tolerance:
                reasons.append(
                    f"nav_diff={actual - expected} > {tolerance}"
                )

            if reasons and len(failures) < 100:
                failures.append(
                    {
                        "snapshot": path.name,
                        "code": code,
                        "name": row.get("name"),
                        "method": row.get("estimated_nav_method"),
                        "stored_nav": (
                            str(expected) if expected is not None else None
                        ),
                        "replayed_nav": (
                            str(actual) if actual is not None else None
                        ),
                        "reasons": reasons,
                    }
                )

    return {
        "version": REPLAY_VERSION,
        "day": day,
        "sample_every": sample_every,
        "snapshot_files_read": len(paths),
        "rows_checked": rows_checked,
        "failed": len(failures),
        "method_counts": dict(method_counts),
        "unique_codes_checked": len(code_counts),
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay normalized Estimated NAV arithmetic from persisted "
            "historical gzip snapshots without network access."
        )
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--day", required=True)
    parser.add_argument("--sample-every", type=int, default=1)
    parser.add_argument(
        "--resolver-class",
        action="append",
        default=[],
    )
    parser.add_argument(
        "--code",
        action="append",
        default=[],
    )
    args = parser.parse_args(argv)

    result = run_replay(
        args.data_root,
        day=args.day,
        sample_every=max(1, args.sample_every),
        resolver_classes=(
            set(args.resolver_class) if args.resolver_class else None
        ),
        codes=set(args.code) if args.code else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
