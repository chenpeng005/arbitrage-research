from __future__ import annotations

from datetime import date, datetime, time as clock_time
import json
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from .snapshot_archive import iter_snapshot_paths, snapshot_id_from_path


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
RETENTION_VERSION = "LOF_SNAPSHOT_RETENTION_V1"
FULL_RETENTION_DAYS = 7
DOWNSAMPLED_RETENTION_DAYS = 60
MIDTERM_CHECKPOINTS = (
    clock_time(10, 0),
    clock_time(11, 0),
    clock_time(14, 0),
    clock_time(14, 50),
    clock_time(15, 0),
)
LONGTERM_CHECKPOINTS = (clock_time(15, 0),)


def _parse_snapshot_time(path: Path) -> datetime | None:
    name = snapshot_id_from_path(path)
    if not name.startswith("runtime-"):
        return None
    raw = name.removeprefix("runtime-")
    try:
        parsed = datetime.strptime(raw, "%Y%m%dT%H%M%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=SHANGHAI_TZ)


def _load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default
    return value if isinstance(value, dict) else default


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _pinned_snapshot_ids(root: Path) -> set[str]:
    payload = _load_json(
        root / "snapshot_retention_pins.json",
        {"snapshot_ids": []},
    )
    return {
        str(value).strip()
        for value in (payload.get("snapshot_ids") or [])
        if str(value).strip()
    }


def _closest_checkpoint(
    items: list[tuple[Path, datetime]],
    target: clock_time,
) -> Path | None:
    if not items:
        return None
    target_seconds = target.hour * 3600 + target.minute * 60 + target.second

    def distance(item: tuple[Path, datetime]) -> tuple[int, datetime]:
        _, ts = item
        seconds = ts.hour * 3600 + ts.minute * 60 + ts.second
        return abs(seconds - target_seconds), ts

    return min(items, key=distance)[0]


def plan_snapshot_retention(
    data_root: str | Path,
    *,
    as_of: date | datetime,
) -> dict:
    root = Path(data_root)
    snapshots_dir = root / "snapshots"
    if isinstance(as_of, datetime):
        today = as_of.astimezone(SHANGHAI_TZ).date()
    else:
        today = as_of

    pinned = _pinned_snapshot_ids(root)
    grouped: dict[date, list[tuple[Path, datetime]]] = {}
    unparsed: list[Path] = []

    for path in iter_snapshot_paths(root):
        ts = _parse_snapshot_time(path)
        if ts is None:
            unparsed.append(path)
            continue
        grouped.setdefault(ts.date(), []).append((path, ts))

    keep: set[Path] = set(unparsed)
    delete: set[Path] = set()
    tiers: dict[str, dict] = {}

    for day, items in sorted(grouped.items()):
        items = sorted(items, key=lambda item: item[1])
        age_days = (today - day).days
        pinned_paths = {
            path
            for path, _ in items
            if snapshot_id_from_path(path) in pinned
        }

        if age_days <= FULL_RETENTION_DAYS:
            selected = {path for path, _ in items}
            tier = "FULL"
        else:
            checkpoints = (
                MIDTERM_CHECKPOINTS
                if age_days <= DOWNSAMPLED_RETENTION_DAYS
                else LONGTERM_CHECKPOINTS
            )
            selected = {
                path
                for target in checkpoints
                if (path := _closest_checkpoint(items, target)) is not None
            }
            # If a day has only unusual/off-hours snapshots, always preserve
            # at least its latest snapshot rather than deleting the entire day.
            if not selected and items:
                selected.add(items[-1][0])
            tier = (
                "MIDTERM_5_PER_DAY"
                if age_days <= DOWNSAMPLED_RETENTION_DAYS
                else "LONGTERM_1_PER_DAY"
            )

        selected |= pinned_paths
        keep |= selected
        delete |= {path for path, _ in items if path not in selected}

        tiers[day.isoformat()] = {
            "tier": tier,
            "age_days": age_days,
            "input_count": len(items),
            "keep_count": len(selected),
            "delete_count": len(items) - len(selected),
        }

    return {
        "version": RETENTION_VERSION,
        "as_of": today.isoformat(),
        "full_retention_days": FULL_RETENTION_DAYS,
        "downsampled_retention_days": DOWNSAMPLED_RETENTION_DAYS,
        "midterm_checkpoints": [
            value.strftime("%H:%M") for value in MIDTERM_CHECKPOINTS
        ],
        "longterm_checkpoints": [
            value.strftime("%H:%M") for value in LONGTERM_CHECKPOINTS
        ],
        "input_count": sum(len(items) for items in grouped.values()),
        "keep_count": len(keep),
        "delete_count": len(delete),
        "keep_paths": sorted(str(path) for path in keep),
        "delete_paths": sorted(str(path) for path in delete),
        "days": tiers,
    }


def apply_snapshot_retention(
    data_root: str | Path,
    *,
    as_of: date | datetime,
) -> dict:
    root = Path(data_root)
    plan = plan_snapshot_retention(root, as_of=as_of)
    deleted = 0
    deleted_bytes = 0
    for value in plan["delete_paths"]:
        path = Path(value)
        try:
            size = path.stat().st_size
            path.unlink()
        except FileNotFoundError:
            continue
        deleted += 1
        deleted_bytes += size

    result = {
        key: value
        for key, value in plan.items()
        if key not in {"keep_paths", "delete_paths"}
    }
    result["deleted_count"] = deleted
    result["deleted_bytes"] = deleted_bytes
    result["applied"] = True
    _atomic_json(root / "snapshot_retention_state.json", result)
    return result


def maybe_apply_snapshot_retention(
    data_root: str | Path,
    *,
    as_of: datetime,
) -> dict | None:
    root = Path(data_root)
    state_path = root / "snapshot_retention_state.json"
    state = _load_json(state_path, {})
    today = as_of.astimezone(SHANGHAI_TZ).date()
    if state.get("as_of") == today.isoformat() and state.get("applied"):
        return None
    return apply_snapshot_retention(root, as_of=today)
