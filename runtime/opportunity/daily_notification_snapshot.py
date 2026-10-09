"""Persist the user-facing daily reminder view as a dated historical snapshot.

This is a presentation/history layer only. It never changes Runtime truth, notification
status, research state, or Change Ledger rows, and it never invokes AI.
"""
from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from runtime.opportunity.incremental_notification_delivery import build_notification_feed
from runtime.opportunity.incremental_read_source import load_opportunity_records

SNAPSHOT_VERSION = "daily-reminder-snapshot-v1"
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _validate_date(value: str) -> str:
    return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()


def _root(data_root: Path) -> Path:
    return data_root / "notification_snapshots"


def _public_root(data_root: Path) -> Path:
    return data_root.parent / "runtime" / "web" / "static" / "notification-history"


def _path(data_root: Path, snapshot_date: str) -> Path:
    return _root(data_root) / f"{_validate_date(snapshot_date)}.json"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def list_daily_notification_snapshots(
    *, data_root: Path, limit: int = 90
) -> list[dict[str, Any]]:
    root = _root(data_root)
    if not root.exists():
        return []
    output: list[dict[str, Any]] = []
    for path in sorted(root.glob("????-??-??.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        output.append(
            {
                "snapshot_date": payload.get("snapshot_date"),
                "captured_at": payload.get("captured_at"),
                "update_status": payload.get("update_status"),
                "market_cutoff": payload.get("market_cutoff"),
                "focus_count": int(payload.get("focus_count") or 0),
                "other_count": int(payload.get("other_count") or 0),
                "opportunity_bond_count": payload.get("opportunity_bond_count"),
                "keep_path_count": payload.get("keep_path_count"),
            }
        )
        if len(output) >= max(1, min(int(limit), 3660)):
            break
    return output


def publish_notification_history_static(*, data_root: Path) -> dict[str, Any]:
    """Mirror persistent snapshots into the already-public static viewer surface."""
    src_root = _root(data_root)
    dst_root = _public_root(data_root)
    dst_root.mkdir(parents=True, exist_ok=True)

    # Remove only dated snapshot files; keep unrelated static assets if added later.
    for old in dst_root.glob("????-??-??.json"):
        old.unlink()

    copied = 0
    if src_root.exists():
        for src in sorted(src_root.glob("????-??-??.json")):
            shutil.copy2(src, dst_root / src.name)
            copied += 1

    items = list_daily_notification_snapshots(data_root=data_root, limit=3660)
    index = {
        "snapshot_version": SNAPSHOT_VERSION,
        "status": "PASS",
        "count": len(items),
        "items": items,
    }
    _atomic_write(dst_root / "index.json", index)
    return {"status": "PASS", "count": copied, "public_root": str(dst_root)}


def capture_daily_notification_snapshot(
    *,
    data_root: Path,
    snapshot_date: str | None = None,
    update_status: str = "PASS",
    source: str = "PRE_TRADE_UNIFIED_UPDATE",
) -> dict[str, Any]:
    """Freeze the reminder page exactly as rendered after a formal daily update.

    Re-running the same calendar date replaces that day's snapshot with the latest
    completed view. This makes repair runs idempotent while preserving the final view
    the user should regard as that day's official reminder page.
    """
    now = datetime.now(CHINA_TZ)
    day = _validate_date(snapshot_date or now.date().isoformat())
    db_path = data_root / "state" / "incremental_runtime.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(f"incremental storage is not initialized: {db_path}")

    feed = build_notification_feed(
        target_db=db_path,
        include_sent=True,
        limit=500,
    )
    try:
        records = load_opportunity_records(data_root=data_root)
    except Exception:
        records = {}

    payload = {
        "snapshot_version": SNAPSHOT_VERSION,
        "status": "PASS",
        "snapshot_date": day,
        "captured_at": now.isoformat(),
        "source": source,
        "update_status": str(update_status or "PASS"),
        "market_cutoff": records.get("market_cutoff"),
        "opportunity_bond_count": records.get("bond_count"),
        "keep_path_count": records.get("keep_path_count"),
        "focus_count": int(feed.get("focus_count") or 0),
        "other_count": int(feed.get("other_count") or 0),
        "feed": feed,
    }
    _atomic_write(_path(data_root, day), payload)
    publish_notification_history_static(data_root=data_root)
    return payload


def load_daily_notification_snapshot(
    *, data_root: Path, snapshot_date: str
) -> dict[str, Any]:
    path = _path(data_root, snapshot_date)
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("notification snapshot root must be object")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--snapshot-date")
    parser.add_argument("--update-status", default="PASS")
    parser.add_argument("--source", default="PRE_TRADE_UNIFIED_UPDATE")
    parser.add_argument("--publish-only", action="store_true")
    args = parser.parse_args()
    data_root = Path(args.data_root)
    if args.publish_only:
        result = publish_notification_history_static(data_root=data_root)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    payload = capture_daily_notification_snapshot(
        data_root=data_root,
        snapshot_date=args.snapshot_date,
        update_status=args.update_status,
        source=args.source,
    )
    print(json.dumps({
        "status": payload["status"],
        "snapshot_date": payload["snapshot_date"],
        "market_cutoff": payload.get("market_cutoff"),
        "focus_count": payload["focus_count"],
        "other_count": payload["other_count"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
