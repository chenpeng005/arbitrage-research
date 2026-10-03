from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable


HISTORY_VERSION = "LOF_ESTIMATE_HISTORY_V1"
VALIDATION_VERSION = "LOF_ESTIMATE_VALIDATION_V1"


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _dump(value: dict) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    ) + "\n"


def _load(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default
    return value if isinstance(value, dict) else default


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


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _iso_text(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value or "")


def _estimate_day(value: Any) -> str | None:
    text = _iso_text(value)
    if len(text) < 10:
        return None
    candidate = text[:10]
    try:
        date.fromisoformat(candidate)
    except ValueError:
        return None
    return candidate


def _history_dir(data_root: str | Path) -> Path:
    return Path(data_root) / "estimate_history"


def _history_path(data_root: str | Path, day: str) -> Path:
    return _history_dir(data_root) / f"{day}.json"


def _ledger_path(data_root: str | Path) -> Path:
    return Path(data_root) / "estimate_validation_ledger.json"


def _history_row(snapshot: dict, row: dict) -> dict | None:
    status = str(row.get("estimated_nav_status") or "")
    nav = _decimal(row.get("estimated_nav"))
    estimate_time = row.get("estimated_nav_time")
    day = _estimate_day(estimate_time)
    code = str(row.get("code") or "").strip()
    if status != "AVAILABLE" or nav is None or nav <= 0 or not day or not code:
        return None
    return {
        "code": code,
        "name": row.get("name"),
        "resolver_class": row.get("resolver_class"),
        "anchor_official_nav": row.get("official_nav"),
        "anchor_official_nav_date": _iso_text(
            row.get("official_nav_date")
        )[:10],
        "estimated_nav": nav,
        "estimated_nav_time": _iso_text(estimate_time),
        "estimated_nav_method": row.get("estimated_nav_method"),
        "estimated_nav_quality": row.get("estimated_nav_quality"),
        "estimated_nav_proxy": row.get("estimated_nav_proxy"),
        "estimated_nav_proxy_time": _iso_text(
            row.get("estimated_nav_proxy_time")
        ),
        "estimated_nav_proxy_return": row.get(
            "estimated_nav_proxy_return"
        ),
        "estimated_nav_fx_return": row.get("estimated_nav_fx_return"),
        "estimated_nav_exposure_ratio": row.get(
            "estimated_nav_exposure_ratio"
        ),
        "estimated_nav_tracking_adjustment": row.get(
            "estimated_nav_tracking_adjustment"
        ),
        "price": row.get("price"),
        "quote_time": _iso_text(row.get("quote_time")),
        "source_snapshot_id": snapshot.get("snapshot_id"),
        "snapshot_generated_at": _iso_text(snapshot.get("generated_at")),
    }


def _merge_history_state(state: dict, snapshot: dict, *, day: str) -> dict:
    rows = dict(state.get("rows") or {})
    for row in snapshot.get("rows") or []:
        candidate = _history_row(snapshot, row)
        if candidate is None:
            continue
        if _estimate_day(candidate["estimated_nav_time"]) != day:
            continue
        code = candidate["code"]
        existing = rows.get(code) or {}
        if candidate["estimated_nav_time"] < str(
            existing.get("estimated_nav_time") or ""
        ):
            continue
        rows[code] = candidate
    return {
        "version": HISTORY_VERSION,
        "date": day,
        "updated_at": _iso_text(snapshot.get("generated_at")),
        "rows": rows,
    }


def record_estimate_history(data_root: str | Path, snapshot: dict) -> dict:
    days = sorted(
        {
            day
            for row in snapshot.get("rows") or []
            if (day := _estimate_day(row.get("estimated_nav_time")))
            and str(row.get("estimated_nav_status") or "") == "AVAILABLE"
            and _decimal(row.get("estimated_nav")) is not None
        }
    )
    result: dict[str, int] = {}
    for day in days:
        path = _history_path(data_root, day)
        state = _load(
            path,
            {
                "version": HISTORY_VERSION,
                "date": day,
                "updated_at": None,
                "rows": {},
            },
        )
        state = _merge_history_state(state, snapshot, day=day)
        _atomic_write(path, _dump(state))
        result[day] = len(state.get("rows") or {})
    return {"days": result}


def load_estimate_history_day(data_root: str | Path, day: str) -> dict:
    return _load(
        _history_path(data_root, day),
        {
            "version": HISTORY_VERSION,
            "date": day,
            "updated_at": None,
            "rows": {},
        },
    )


def _truth_signature(snapshot: dict) -> str:
    parts = []
    for row in snapshot.get("rows") or []:
        nav = _decimal(row.get("official_nav"))
        day = _iso_text(row.get("official_nav_date"))[:10]
        code = str(row.get("code") or "").strip()
        if not code or nav is None or nav <= 0 or len(day) != 10:
            continue
        parts.append(f"{code}|{day}|{nav}")
    return hashlib.sha256("\n".join(sorted(parts)).encode("utf-8")).hexdigest()


def _p90(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(0.90 * len(ordered)) - 1)
    return ordered[index]


def _aggregate(observations: Iterable[dict]) -> dict:
    rows = list(observations)
    if not rows:
        return {
            "sample_count": 0,
            "mae_pct": None,
            "p90_abs_error_pct": None,
            "bias_pct": None,
            "last_error_pct": None,
            "last_truth_date": None,
        }
    errors = [float(row["error_pct"]) for row in rows]
    absolute = [abs(value) for value in errors]
    latest = max(rows, key=lambda row: str(row.get("truth_date") or ""))
    return {
        "sample_count": len(rows),
        "mae_pct": sum(absolute) / len(absolute),
        "p90_abs_error_pct": _p90(absolute),
        "bias_pct": sum(errors) / len(errors),
        "last_error_pct": float(latest["error_pct"]),
        "last_truth_date": latest.get("truth_date"),
    }


def update_estimate_validation_ledger(
    data_root: str | Path,
    snapshot: dict,
) -> dict:
    path = _ledger_path(data_root)
    ledger = _load(
        path,
        {
            "version": VALIDATION_VERSION,
            "truth_signature": None,
            "updated_at": None,
            "observations": {},
            "rows": {},
            "methods": {},
            "summary": {},
        },
    )
    signature = _truth_signature(snapshot)
    if signature and signature == ledger.get("truth_signature"):
        return ledger

    observations = dict(ledger.get("observations") or {})
    history_cache: dict[str, dict] = {}
    for row in snapshot.get("rows") or []:
        code = str(row.get("code") or "").strip()
        truth = _decimal(row.get("official_nav"))
        truth_date = _iso_text(row.get("official_nav_date"))[:10]
        if not code or truth is None or truth <= 0 or len(truth_date) != 10:
            continue

        if truth_date not in history_cache:
            history_cache[truth_date] = load_estimate_history_day(
                data_root,
                truth_date,
            )
        estimate = (
            history_cache[truth_date].get("rows") or {}
        ).get(code)
        if not estimate:
            continue
        estimated_nav = _decimal(estimate.get("estimated_nav"))
        if estimated_nav is None or estimated_nav <= 0:
            continue

        error_pct = float(
            (estimated_nav / truth - Decimal("1")) * Decimal("100")
        )
        key = f"{code}|{truth_date}"
        observations[key] = {
            "code": code,
            "name": row.get("name") or estimate.get("name"),
            "resolver_class": (
                estimate.get("resolver_class")
                or row.get("resolver_class")
            ),
            "method": estimate.get("estimated_nav_method"),
            "proxy": estimate.get("estimated_nav_proxy"),
            "truth_date": truth_date,
            "official_nav": float(truth),
            "estimated_nav": float(estimated_nav),
            "estimated_nav_time": estimate.get("estimated_nav_time"),
            "error_pct": error_pct,
            "abs_error_pct": abs(error_pct),
            "source_snapshot_id": estimate.get("source_snapshot_id"),
        }

    by_code: dict[str, list[dict]] = {}
    by_method: dict[str, list[dict]] = {}
    for observation in observations.values():
        code = str(observation.get("code") or "")
        method = str(observation.get("method") or "UNKNOWN")
        by_code.setdefault(code, []).append(observation)
        by_method.setdefault(method, []).append(observation)

    rows = {
        code: {
            "code": code,
            "name": max(
                values,
                key=lambda row: str(row.get("truth_date") or ""),
            ).get("name"),
            "resolver_class": max(
                values,
                key=lambda row: str(row.get("truth_date") or ""),
            ).get("resolver_class"),
            **_aggregate(values),
        }
        for code, values in by_code.items()
    }
    methods = {
        method: {
            "method": method,
            **_aggregate(values),
        }
        for method, values in by_method.items()
    }
    ledger = {
        "version": VALIDATION_VERSION,
        "truth_signature": signature,
        "updated_at": _iso_text(snapshot.get("generated_at")),
        "observations": observations,
        "rows": rows,
        "methods": methods,
        "summary": {
            "observation_count": len(observations),
            "fund_count": len(rows),
            "method_count": len(methods),
        },
    }
    _atomic_write(path, _dump(ledger))
    return ledger


def load_estimate_validation_summary(data_root: str | Path) -> dict:
    ledger = _load(
        _ledger_path(data_root),
        {
            "version": VALIDATION_VERSION,
            "updated_at": None,
            "rows": {},
            "methods": {},
            "summary": {
                "observation_count": 0,
                "fund_count": 0,
                "method_count": 0,
            },
        },
    )
    return {
        "version": VALIDATION_VERSION,
        "updated_at": ledger.get("updated_at"),
        "rows": ledger.get("rows") or {},
        "methods": ledger.get("methods") or {},
        "summary": ledger.get("summary") or {},
    }


def rebuild_estimate_history(data_root: str | Path) -> dict:
    root = Path(data_root)
    day_states: dict[str, dict] = {}
    paths = sorted((root / "snapshots").glob("runtime-*.json"))
    read_count = 0
    for path in paths:
        try:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(snapshot, dict):
            continue
        read_count += 1
        days = {
            day
            for row in snapshot.get("rows") or []
            if (day := _estimate_day(row.get("estimated_nav_time")))
            and str(row.get("estimated_nav_status") or "") == "AVAILABLE"
            and _decimal(row.get("estimated_nav")) is not None
        }
        for day in days:
            state = day_states.setdefault(
                day,
                {
                    "version": HISTORY_VERSION,
                    "date": day,
                    "updated_at": None,
                    "rows": {},
                },
            )
            day_states[day] = _merge_history_state(
                state,
                snapshot,
                day=day,
            )

    for day, state in day_states.items():
        _atomic_write(_history_path(root, day), _dump(state))

    latest_path = root / "latest_market_snapshot.json"
    latest = _load(latest_path, {})
    ledger = (
        update_estimate_validation_ledger(root, latest)
        if latest
        else {}
    )
    return {
        "snapshot_files_read": read_count,
        "history_days": {
            day: len(state.get("rows") or {})
            for day, state in sorted(day_states.items())
        },
        "validation_summary": ledger.get("summary") or {},
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build LOF estimate history and validation ledger."
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="rebuild daily estimate history from archived snapshots",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.data_root)
    if args.rebuild:
        result = rebuild_estimate_history(root)
    else:
        latest = _load(root / "latest_market_snapshot.json", {})
        if not latest:
            raise SystemExit("latest market snapshot unavailable")
        record_estimate_history(root, latest)
        result = update_estimate_validation_ledger(root, latest)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
