from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import date, datetime, time as clock_time
import json
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from .snapshot_archive import iter_snapshot_paths, read_snapshot_json


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
AUDIT_VERSION = "LOF_ESTIMATE_FRESHNESS_AUDIT_V1"
CADENCE_PROFILE_VERSION = "LOF_ESTIMATE_CADENCE_PROFILE_V1"
TARGET_MARKET_CYCLE_SECONDS = 30
TARGET_OFF_HOURS_CYCLE_SECONDS = 300


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


def _market_refresh_window(value: datetime) -> bool:
    local = value.astimezone(SHANGHAI_TZ)
    if local.weekday() >= 5:
        return False
    current = local.time()
    return (
        clock_time(9, 25) <= current <= clock_time(11, 35)
        or clock_time(12, 55) <= current <= clock_time(15, 5)
    )


def _latest_exchange_quote_date(snapshot: dict) -> date | None:
    dates: list[date] = []
    for row in snapshot.get("rows") or []:
        parsed = _parse_datetime(row.get("quote_time"))
        if parsed is not None:
            dates.append(parsed.date())
    return max(dates) if dates else None


def market_context(snapshot: dict) -> dict:
    generated = _parse_datetime(snapshot.get("generated_at"))
    if generated is None:
        return {
            "state": "UNKNOWN",
            "generated_date": None,
            "latest_exchange_quote_date": None,
            "target_cycle_seconds": TARGET_OFF_HOURS_CYCLE_SECONDS,
        }

    quote_date = _latest_exchange_quote_date(snapshot)
    if quote_date is not None and quote_date < generated.date():
        state = "OFF_MARKET_OR_HOLIDAY"
    elif _market_refresh_window(generated):
        state = "DOMESTIC_MARKET_REFRESH_WINDOW"
    else:
        state = "OFF_HOURS"

    return {
        "state": state,
        "generated_date": generated.date().isoformat(),
        "latest_exchange_quote_date": (
            quote_date.isoformat() if quote_date else None
        ),
        "target_cycle_seconds": (
            TARGET_MARKET_CYCLE_SECONDS
            if state == "DOMESTIC_MARKET_REFRESH_WINDOW"
            else TARGET_OFF_HOURS_CYCLE_SECONDS
        ),
    }


def _coverage_state(row: dict) -> str:
    method = str(row.get("estimated_nav_method") or "")
    if not method or method == "UNAVAILABLE":
        return "NO_MAIN_MODEL"
    return "MAIN_MODEL_CONFIGURED"


def _no_model_reason(row: dict) -> str:
    error = str(row.get("estimated_nav_error") or "")
    if error:
        return error
    resolver = str(row.get("resolver_class") or "UNKNOWN")
    if resolver == "R2_DOMESTIC_OTHER":
        return "R2_NOT_PROMOTED_TO_MAIN"
    if resolver == "R4_QDII_OTHER":
        return "R4_MAIN_RESOLVER_NOT_IMPLEMENTED"
    if resolver == "R5_SPECIAL":
        return "R5_MAIN_MODEL_NOT_CONFIGURED"
    return "MAIN_MODEL_NOT_CONFIGURED"


def _configured_unavailable_reason(row: dict) -> str:
    error = str(row.get("estimated_nav_error") or "")
    if error:
        return error
    method = str(row.get("estimated_nav_method") or "")
    if method in {
        "DISCLOSED_HOLDINGS_BASKET",
        "R2B2_CASH_HEAVY_HOLDINGS_BASKET",
        "RISK_ASSET_OVERLAY",
    }:
        return "PROMOTED_SHADOW_INPUT_UNAVAILABLE"
    if method in {
        "MULTIDAY_PROXY_FX_BRIDGE",
        "HK_LIVE_INDEX_FX_BRIDGE",
        "US_FUTURES_FX_BRIDGE",
        "US_LAST_CLOSE_FX_BRIDGE",
    }:
        return "QDII_BRIDGE_INPUT_UNAVAILABLE"
    if method in {
        "COMMODITY_FX_BRIDGE",
        "DOMESTIC_FUTURES_PREV_SETTLEMENT",
    }:
        return "SPECIAL_PROXY_INPUT_UNAVAILABLE"
    return "MODEL_INPUT_UNAVAILABLE"


def _diagnostic_flags(row: dict) -> list[str]:
    flags: list[str] = []
    estimate_time = _parse_datetime(row.get("estimated_nav_time"))
    proxy_time = _parse_datetime(row.get("estimated_nav_proxy_time"))
    if estimate_time is not None and proxy_time is not None:
        lag = (proxy_time - estimate_time).total_seconds()
        if lag > 90:
            flags.append("PROXY_NEWER_THAN_ESTIMATE")
    quote_time = _parse_datetime(row.get("quote_time"))
    if estimate_time is not None and quote_time is not None:
        lag = (quote_time - estimate_time).total_seconds()
        if lag > 90:
            flags.append("EXCHANGE_QUOTE_NEWER_THAN_ESTIMATE")
    return flags


def classify_row(row: dict, *, context: dict) -> dict:
    coverage = _coverage_state(row)
    status = str(row.get("estimated_nav_status") or "UNAVAILABLE")
    flags = _diagnostic_flags(row)

    if coverage == "NO_MAIN_MODEL":
        update_state = "STRUCTURAL_NO_MAIN_MODEL"
        blocker = _no_model_reason(row)
    elif status == "AVAILABLE":
        update_state = "CURRENTLY_AVAILABLE"
        blocker = None
    elif status == "STALE":
        if context.get("state") != "DOMESTIC_MARKET_REFRESH_WINDOW":
            update_state = "EXPECTED_OFF_MARKET_STALE"
            blocker = "OFF_MARKET_OR_HOLIDAY"
        else:
            update_state = "STALE_DURING_ACTIVE_SESSION"
            blocker = (
                str(row.get("estimated_nav_error") or "")
                or (
                    "QUOTE_STALE"
                    if row.get("quote_status") == "STALE"
                    else "ESTIMATE_NOT_REFRESHED"
                )
            )
    else:
        update_state = "CONFIGURED_BUT_UNAVAILABLE"
        blocker = _configured_unavailable_reason(row)

    return {
        "code": row.get("code"),
        "name": row.get("name"),
        "lof_type": row.get("lof_type"),
        "resolver_class": row.get("resolver_class"),
        "coverage_state": coverage,
        "update_state": update_state,
        "blocker": blocker,
        "estimated_nav_status": status,
        "estimated_nav_method": row.get("estimated_nav_method"),
        "estimated_model_id": row.get("estimated_model_id"),
        "estimated_model_version": row.get("estimated_model_version"),
        "estimated_nav": row.get("estimated_nav"),
        "estimated_nav_time": row.get("estimated_nav_time"),
        "estimated_nav_age_seconds": row.get("estimated_nav_age_seconds"),
        "estimated_nav_proxy": row.get("estimated_nav_proxy"),
        "estimated_nav_proxy_time": row.get("estimated_nav_proxy_time"),
        "quote_status": row.get("quote_status"),
        "quote_time": row.get("quote_time"),
        "quote_age_seconds": row.get("quote_age_seconds"),
        "official_nav_date": row.get("official_nav_date"),
        "official_nav_lag_label": row.get("official_nav_lag_label"),
        "last_estimated_nav": row.get("last_estimated_nav"),
        "last_estimated_nav_time": row.get("last_estimated_nav_time"),
        "last_estimated_nav_method": row.get("last_estimated_nav_method"),
        "diagnostic_flags": flags,
        "target_runtime_cycle_seconds": TARGET_MARKET_CYCLE_SECONDS,
    }


def _path_day(path: Path) -> date | None:
    name = path.name
    marker = "runtime-"
    if not name.startswith(marker):
        return None
    raw = name[len(marker):len(marker) + 8]
    try:
        return datetime.strptime(raw, "%Y%m%d").date()
    except ValueError:
        return None


def _fresh_quote_count(snapshot: dict, day: date) -> int:
    count = 0
    for row in snapshot.get("rows") or []:
        if row.get("quote_status") != "AVAILABLE":
            continue
        parsed = _parse_datetime(row.get("quote_time"))
        if parsed is not None and parsed.date() == day:
            count += 1
    return count


def select_latest_cadence_baseline_date(
    data_root: str | Path,
) -> date | None:
    paths = iter_snapshot_paths(data_root)
    grouped: dict[date, list[Path]] = defaultdict(list)
    for path in paths:
        day = _path_day(path)
        if day is not None:
            grouped[day].append(path)

    for day in sorted(grouped, reverse=True):
        day_paths = sorted(grouped[day])
        if len(day_paths) < 20:
            continue
        candidates = [
            day_paths[len(day_paths) // 2],
            day_paths[-1],
        ]
        for path in candidates:
            try:
                snapshot = read_snapshot_json(path)
            except Exception:
                continue
            if _fresh_quote_count(snapshot, day) >= 50:
                return day
    return None


def build_cadence_profile(
    data_root: str | Path,
    *,
    baseline_date: date | None = None,
) -> dict:
    root = Path(data_root)
    baseline_date = (
        baseline_date
        or select_latest_cadence_baseline_date(root)
    )
    if baseline_date is None:
        return {
            "version": CADENCE_PROFILE_VERSION,
            "baseline_date": None,
            "snapshot_files_read": 0,
            "rows": {},
            "methods": {},
        }

    paths = [
        path
        for path in iter_snapshot_paths(root)
        if _path_day(path) == baseline_date
    ]
    observations: dict[str, list[datetime]] = defaultdict(list)
    methods: dict[str, str] = {}
    model_versions: dict[str, str | None] = {}
    names: dict[str, str | None] = {}

    for path in paths:
        try:
            snapshot = read_snapshot_json(path)
        except Exception:
            continue
        for row in snapshot.get("rows") or []:
            method = str(row.get("estimated_nav_method") or "")
            if not method or method == "UNAVAILABLE":
                continue
            estimate_time = _parse_datetime(row.get("estimated_nav_time"))
            if estimate_time is None:
                continue
            local_time = estimate_time.time()
            if not (
                clock_time(9, 25) <= local_time <= clock_time(11, 35)
                or clock_time(12, 55) <= local_time <= clock_time(15, 5)
            ):
                continue
            code = str(row.get("code") or "")
            if not code:
                continue
            observations[code].append(estimate_time)
            methods[code] = method
            model_versions[code] = row.get("estimated_model_version")
            names[code] = row.get("name")

    rows: dict[str, dict] = {}
    by_method: dict[str, list[float]] = defaultdict(list)
    by_method_updates: dict[str, list[int]] = defaultdict(list)

    for code, values in observations.items():
        unique = sorted(set(values))
        intervals = [
            (right - left).total_seconds()
            for left, right in zip(unique, unique[1:])
            if 0 < (right - left).total_seconds() <= 1800
        ]
        observed = median(intervals) if intervals else None
        method = methods[code]
        rows[code] = {
            "code": code,
            "name": names.get(code),
            "method": method,
            "model_version": model_versions.get(code),
            "baseline_date": baseline_date.isoformat(),
            "unique_update_count": len(unique),
            "observed_median_update_seconds": observed,
            "first_estimate_time": (
                unique[0].isoformat() if unique else None
            ),
            "last_estimate_time": (
                unique[-1].isoformat() if unique else None
            ),
        }
        if observed is not None:
            by_method[method].append(float(observed))
            by_method_updates[method].append(len(unique))

    method_rows: dict[str, dict] = {}
    for method in sorted(set(methods.values())):
        medians = by_method.get(method, [])
        updates = by_method_updates.get(method, [])
        method_rows[method] = {
            "method": method,
            "fund_count": sum(
                1 for value in rows.values()
                if value.get("method") == method
            ),
            "funds_with_interval": len(medians),
            "median_update_seconds": (
                median(medians) if medians else None
            ),
            "median_unique_update_count": (
                median(updates) if updates else None
            ),
            "target_runtime_cycle_seconds": TARGET_MARKET_CYCLE_SECONDS,
        }

    return {
        "version": CADENCE_PROFILE_VERSION,
        "baseline_date": baseline_date.isoformat(),
        "snapshot_files_read": len(paths),
        "rows": rows,
        "methods": method_rows,
    }


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default
    return value if isinstance(value, dict) else default


def write_cadence_profile(
    data_root: str | Path,
    profile: dict,
) -> Path:
    path = Path(data_root) / "estimate_freshness_cadence.json"
    path.write_text(
        json.dumps(profile, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return path


def build_freshness_audit(
    *,
    snapshot: dict,
    cadence_profile: dict | None = None,
) -> dict:
    context = market_context(snapshot)
    cadence_profile = cadence_profile or {}
    cadence_rows = cadence_profile.get("rows") or {}

    rows: list[dict] = []
    for source in snapshot.get("rows") or []:
        row = classify_row(source, context=context)
        cadence = cadence_rows.get(str(row.get("code") or ""))
        if (
            cadence
            and cadence.get("method") == row.get("estimated_nav_method")
            and (
                not cadence.get("model_version")
                or not row.get("estimated_model_version")
                or cadence.get("model_version") == row.get("estimated_model_version")
            )
        ):
            row["observed_cadence_date"] = cadence.get("baseline_date")
            row["observed_median_update_seconds"] = cadence.get(
                "observed_median_update_seconds"
            )
            row["observed_unique_update_count"] = cadence.get(
                "unique_update_count"
            )
            observed = row["observed_median_update_seconds"]
            row["cadence_state"] = (
                "PASS"
                if observed is not None
                and float(observed) <= 45
                else "SLOW"
                if observed is not None
                else "NO_BASELINE"
            )
        else:
            row["observed_cadence_date"] = None
            row["observed_median_update_seconds"] = None
            row["observed_unique_update_count"] = None
            row["cadence_state"] = "NO_BASELINE"
        rows.append(row)

    coverage = Counter(row["coverage_state"] for row in rows)
    update_states = Counter(row["update_state"] for row in rows)
    blockers = Counter(
        str(row["blocker"])
        for row in rows
        if row.get("blocker")
    )
    flags = Counter(
        flag
        for row in rows
        for flag in row.get("diagnostic_flags") or []
    )
    cadence_states = Counter(row["cadence_state"] for row in rows)

    return {
        "version": AUDIT_VERSION,
        "snapshot_id": snapshot.get("snapshot_id"),
        "generated_at": snapshot.get("generated_at"),
        "market_context": context,
        "cadence_profile": {
            "version": cadence_profile.get("version"),
            "baseline_date": cadence_profile.get("baseline_date"),
            "snapshot_files_read": cadence_profile.get(
                "snapshot_files_read"
            ),
            "methods": cadence_profile.get("methods") or {},
        },
        "summary": {
            "universe_count": len(rows),
            "main_model_configured_count": coverage.get(
                "MAIN_MODEL_CONFIGURED", 0
            ),
            "no_main_model_count": coverage.get("NO_MAIN_MODEL", 0),
            "currently_available_count": update_states.get(
                "CURRENTLY_AVAILABLE", 0
            ),
            "expected_off_market_stale_count": update_states.get(
                "EXPECTED_OFF_MARKET_STALE", 0
            ),
            "active_session_stale_count": update_states.get(
                "STALE_DURING_ACTIVE_SESSION", 0
            ),
            "configured_but_unavailable_count": update_states.get(
                "CONFIGURED_BUT_UNAVAILABLE", 0
            ),
            "structural_no_main_model_count": update_states.get(
                "STRUCTURAL_NO_MAIN_MODEL", 0
            ),
            "cadence_pass_count": cadence_states.get("PASS", 0),
            "cadence_slow_count": cadence_states.get("SLOW", 0),
            "cadence_no_baseline_count": cadence_states.get(
                "NO_BASELINE", 0
            ),
            "blockers": dict(blockers.most_common()),
            "diagnostic_flags": dict(flags.most_common()),
        },
        "rows": rows,
    }


def load_freshness_audit(
    data_root: str | Path,
    *,
    snapshot: dict,
) -> dict:
    root = Path(data_root)
    profile = _load_json(
        root / "estimate_freshness_cadence.json",
        {},
    )
    return build_freshness_audit(
        snapshot=snapshot,
        cadence_profile=profile,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit LOF Estimated NAV coverage, freshness and cadence."
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument(
        "--rebuild-cadence",
        action="store_true",
    )
    parser.add_argument(
        "--baseline-date",
        default=None,
    )
    args = parser.parse_args(argv)

    root = Path(args.data_root)
    if args.rebuild_cadence:
        baseline = (
            date.fromisoformat(args.baseline_date)
            if args.baseline_date
            else None
        )
        profile = build_cadence_profile(
            root,
            baseline_date=baseline,
        )
        write_cadence_profile(root, profile)

    snapshot = _load_json(
        root / "latest_market_snapshot.json",
        {},
    )
    if not snapshot:
        raise SystemExit("latest_market_snapshot.json unavailable")
    result = load_freshness_audit(
        root,
        snapshot=snapshot,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
