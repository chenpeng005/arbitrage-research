from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
LEDGER_VERSION = "LOF_SHADOW_VALIDATION_LEDGER_V0"
MARKET_SNAPSHOT_GLOB = "runtime-*.json"

SOURCE_DIRS = {
    "R2A_HOLDINGS": ("r2a_shadow_snapshots",),
    "R2C_RISK_OVERLAY": ("r2c_risk_overlay_snapshots",),
    "R2C_LIVE_164814": ("r2c_live_overlay", "snapshots"),
    "R4_INDIA": ("r4_india_shadow", "snapshots"),
    "R4_USD_BOND_501300": ("r4_usd_bond_shadow", "snapshots"),
}


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=SHANGHAI_TZ)
    return result.astimezone(SHANGHAI_TZ)


def _positive_float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def _time_bin(value: datetime, minutes: int = 30) -> str:
    minute = value.minute - value.minute % minutes
    return f"{value.hour:02d}:{minute:02d}"


def _quantile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    rows = sorted(values)
    if len(rows) == 1:
        return rows[0]
    k = (len(rows) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return rows[lo]
    return rows[lo] * (hi - k) + rows[hi] * (k - lo)


def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _default_state() -> dict:
    return {
        "version": LEDGER_VERSION,
        "updated_at": None,
        "market_snapshot_cursor": None,
        "shadow_snapshot_cursors": {},
        "actual_navs": {},
        "observations": {},
        "summaries": {},
    }


def load_state(state_root: str | Path) -> dict:
    path = Path(state_root) / "shadow_validation_ledger.json"
    payload = _load_json(path)
    if payload is None or payload.get("version") != LEDGER_VERSION:
        return _default_state()
    payload.setdefault("market_snapshot_cursor", None)
    payload.setdefault("shadow_snapshot_cursors", {})
    payload.setdefault("actual_navs", {})
    payload.setdefault("observations", {})
    payload.setdefault("summaries", {})
    return payload


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


def persist_state(state_root: str | Path, state: dict) -> Path:
    path = Path(state_root) / "shadow_validation_ledger.json"
    _atomic_write(
        path,
        json.dumps(
            state,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        + "\n",
    )
    return path


def ingest_market_truth(
    *,
    data_root: str | Path,
    state: dict,
    initial_snapshot_limit: int = 500,
) -> int:
    snapshot_dir = Path(data_root) / "snapshots"
    files = sorted(snapshot_dir.glob(MARKET_SNAPSHOT_GLOB))
    cursor = state.get("market_snapshot_cursor")
    if cursor:
        files = [path for path in files if path.name > cursor]
    elif len(files) > initial_snapshot_limit:
        files = files[-initial_snapshot_limit:]

    added = 0
    actual_navs = state["actual_navs"]
    for path in files:
        snapshot = _load_json(path)
        if snapshot is None:
            continue
        observed_at = snapshot.get("generated_at") or snapshot.get("market_cutoff")
        snapshot_id = snapshot.get("snapshot_id") or path.stem
        for row in snapshot.get("rows") or []:
            code = str(row.get("code") or "").strip()
            nav_date = str(row.get("official_nav_date") or "").strip()
            nav = _positive_float(row.get("official_nav"))
            if not code or not nav_date or nav is None:
                continue
            key = f"{code}|{nav_date}"
            previous = actual_navs.get(key)
            item = {
                "fund_code": code,
                "nav_date": nav_date,
                "nav": nav,
                "source": row.get("official_nav_source"),
                "observed_at": observed_at,
                "snapshot_id": snapshot_id,
            }
            if previous != item:
                actual_navs[key] = item
                added += 1
        state["market_snapshot_cursor"] = path.name
    return added


def _estimate_variants(source: str, row: dict) -> list[tuple[str, float]]:
    if source == "R4_INDIA":
        result: list[tuple[str, float]] = []
        sensex = _positive_float(
            row.get("shadow_estimated_nav_sensex")
            if row.get("shadow_estimated_nav_sensex") is not None
            else row.get("shadow_estimated_nav")
        )
        if sensex is not None:
            result.append(("SENSEX", sensex))
        fx = _positive_float(row.get("shadow_estimated_nav_sensex_inr_cny"))
        if fx is not None:
            result.append(("SENSEX_INR_CNY", fx))
        return result

    value = _positive_float(row.get("shadow_estimated_nav"))
    if value is None:
        return []
    method = str(row.get("method") or source)
    return [(method, value)]


def _observation_payload(
    *,
    source: str,
    variant: str,
    snapshot: dict,
    row: dict,
    estimated_nav: float,
) -> tuple[str, dict] | None:
    generated_at = _parse_dt(snapshot.get("generated_at"))
    code = str(row.get("fund_code") or row.get("code") or "").strip()
    if generated_at is None or not code:
        return None

    target_date = generated_at.date().isoformat()
    sample_bin = _time_bin(generated_at)
    model_id = f"{source}:{variant}"
    observation_id = f"{model_id}|{code}|{target_date}|{sample_bin}"

    metadata_keys = (
        "quality",
        "quality_candidate",
        "research_group",
        "driver",
        "holdings_as_of_date",
        "fresh_coverage_ratio",
        "proxy_source",
        "proxy_time",
        "fx_bridge_status",
        "fx_bridge_quality",
        "us10y_time",
        "usdcny_time",
        "market_quote_status",
    )
    metadata = {
        key: row.get(key)
        for key in metadata_keys
        if row.get(key) is not None
    }

    return observation_id, {
        "observation_id": observation_id,
        "source": source,
        "variant": variant,
        "model_id": model_id,
        "fund_code": code,
        "fund_name": row.get("fund_name") or row.get("name") or code,
        "target_nav_date": target_date,
        "sample_bin": sample_bin,
        "generated_at": generated_at.isoformat(),
        "snapshot_id": snapshot.get("snapshot_id"),
        "estimated_nav": estimated_nav,
        "shadow_premium_rate": row.get("shadow_premium_rate"),
        "market_price": row.get("market_price"),
        "base_official_nav": row.get("official_nav"),
        "base_official_nav_date": row.get("official_nav_date"),
        "metadata": metadata,
        "actual_nav": None,
        "truth_source": None,
        "truth_snapshot_id": None,
        "error_pct": None,
        "abs_error_pct": None,
    }


def ingest_shadow_snapshots(
    *,
    data_root: str | Path,
    state: dict,
) -> int:
    root = Path(data_root)
    observations = state["observations"]
    cursors = state["shadow_snapshot_cursors"]
    changed = 0

    for source, parts in SOURCE_DIRS.items():
        directory = root.joinpath(*parts)
        files = sorted(directory.glob("*.json"))
        cursor = cursors.get(source)
        if cursor:
            files = [path for path in files if path.name > cursor]

        for path in files:
            snapshot = _load_json(path)
            if snapshot is None:
                continue
            for row in snapshot.get("rows") or []:
                if str(row.get("status") or "") != "AVAILABLE":
                    continue
                for variant, estimated_nav in _estimate_variants(source, row):
                    built = _observation_payload(
                        source=source,
                        variant=variant,
                        snapshot=snapshot,
                        row=row,
                        estimated_nav=estimated_nav,
                    )
                    if built is None:
                        continue
                    observation_id, item = built
                    previous = observations.get(observation_id)
                    if (
                        previous is None
                        or str(item["generated_at"]) > str(previous.get("generated_at") or "")
                    ):
                        observations[observation_id] = item
                        changed += 1
            cursors[source] = path.name
    return changed


def resolve_observations(state: dict) -> int:
    resolved = 0
    actual_navs = state["actual_navs"]
    for item in state["observations"].values():
        key = f"{item['fund_code']}|{item['target_nav_date']}"
        truth = actual_navs.get(key)
        if truth is None:
            continue
        actual = _positive_float(truth.get("nav"))
        estimated = _positive_float(item.get("estimated_nav"))
        if actual is None or estimated is None:
            continue
        error_pct = (estimated / actual - 1.0) * 100.0
        changed = (
            item.get("actual_nav") != actual
            or item.get("error_pct") != error_pct
        )
        item["actual_nav"] = actual
        item["truth_source"] = truth.get("source")
        item["truth_snapshot_id"] = truth.get("snapshot_id")
        item["error_pct"] = error_pct
        item["abs_error_pct"] = abs(error_pct)
        if changed:
            resolved += 1
    return resolved


def build_summaries(state: dict) -> dict:
    groups: dict[str, list[dict]] = defaultdict(list)
    for item in state["observations"].values():
        key = f"{item['model_id']}|{item['fund_code']}"
        groups[key].append(item)

    summaries: dict[str, dict] = {}
    for key, rows in sorted(groups.items()):
        evaluated = [
            row
            for row in rows
            if row.get("error_pct") is not None
        ]
        pending = len(rows) - len(evaluated)
        abs_errors = [float(row["abs_error_pct"]) for row in evaluated]
        errors = [float(row["error_pct"]) for row in evaluated]

        daily_abs: dict[str, list[float]] = defaultdict(list)
        for row in evaluated:
            daily_abs[str(row["target_nav_date"])].append(
                float(row["abs_error_pct"])
            )
        daily_maes = [
            sum(values) / len(values)
            for values in daily_abs.values()
        ]

        first = rows[0]
        summaries[key] = {
            "model_id": first["model_id"],
            "fund_code": first["fund_code"],
            "fund_name": first["fund_name"],
            "observation_count": len(rows),
            "evaluated_count": len(evaluated),
            "pending_count": pending,
            "evaluated_day_count": len(daily_abs),
            "mae_pct": (
                sum(abs_errors) / len(abs_errors)
                if abs_errors
                else None
            ),
            "daily_mae_pct": (
                sum(daily_maes) / len(daily_maes)
                if daily_maes
                else None
            ),
            "median_abs_error_pct": _quantile(abs_errors, 0.50),
            "p90_abs_error_pct": _quantile(abs_errors, 0.90),
            "max_abs_error_pct": max(abs_errors) if abs_errors else None,
            "bias_pct": (
                sum(errors) / len(errors)
                if errors
                else None
            ),
            "latest_target_nav_date": max(
                str(row["target_nav_date"]) for row in rows
            ),
        }
    state["summaries"] = summaries
    return summaries


def run_once(
    *,
    data_root: str | Path,
    state_root: str | Path,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(SHANGHAI_TZ)
    state = load_state(state_root)
    truth_updates = ingest_market_truth(
        data_root=data_root,
        state=state,
    )
    observation_updates = ingest_shadow_snapshots(
        data_root=data_root,
        state=state,
    )
    resolved_updates = resolve_observations(state)
    summaries = build_summaries(state)
    state["updated_at"] = now.isoformat()
    path = persist_state(state_root, state)

    return {
        "status": "PASS",
        "version": LEDGER_VERSION,
        "updated_at": state["updated_at"],
        "truth_count": len(state["actual_navs"]),
        "observation_count": len(state["observations"]),
        "evaluated_count": sum(
            item.get("error_pct") is not None
            for item in state["observations"].values()
        ),
        "pending_count": sum(
            item.get("error_pct") is None
            for item in state["observations"].values()
        ),
        "summary_count": len(summaries),
        "truth_updates": truth_updates,
        "observation_updates": observation_updates,
        "resolved_updates": resolved_updates,
        "path": str(path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a research-only validation ledger for LOF shadow NAV models."
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--state-root", required=True)
    args = parser.parse_args(argv)

    result = run_once(
        data_root=args.data_root,
        state_root=args.state_root,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
