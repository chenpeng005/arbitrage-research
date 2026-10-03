from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from .snapshot_archive import (
    ARCHIVE_FORMAT_VERSION,
    compress_existing_snapshot,
    iter_snapshot_paths,
    snapshot_archive_stats,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
COMPRESSION_MIGRATION_VERSION = "LOF_SNAPSHOT_COMPRESSION_MIGRATION_V1"


def plan_snapshot_compression(data_root: str | Path) -> dict:
    root = Path(data_root)
    snapshots_dir = root / "snapshots"
    raw_paths = sorted(snapshots_dir.glob("runtime-*.json"))
    compressed_paths = sorted(snapshots_dir.glob("runtime-*.json.gz"))
    raw_bytes = sum(path.stat().st_size for path in raw_paths)
    compressed_bytes = sum(path.stat().st_size for path in compressed_paths)
    return {
        "version": COMPRESSION_MIGRATION_VERSION,
        "archive_format_version": ARCHIVE_FORMAT_VERSION,
        "raw_count": len(raw_paths),
        "compressed_count": len(compressed_paths),
        "raw_bytes": raw_bytes,
        "compressed_bytes": compressed_bytes,
        "total_snapshot_count": len(iter_snapshot_paths(root)),
    }


def apply_snapshot_compression(data_root: str | Path) -> dict:
    root = Path(data_root)
    before = plan_snapshot_compression(root)
    raw_paths = sorted((root / "snapshots").glob("runtime-*.json"))

    migrated_count = 0
    source_bytes = 0
    stored_bytes = 0
    ratios: list[float] = []

    for path in raw_paths:
        result = compress_existing_snapshot(path)
        if result.get("status") != "COMPRESSED":
            continue
        migrated_count += 1
        raw_bytes = int(result.get("raw_bytes") or 0)
        compressed_bytes = int(result.get("compressed_bytes") or 0)
        source_bytes += raw_bytes
        stored_bytes += compressed_bytes
        ratio = result.get("ratio")
        if ratio is not None:
            ratios.append(float(ratio))

    after = snapshot_archive_stats(root)
    result = {
        "version": COMPRESSION_MIGRATION_VERSION,
        "archive_format_version": ARCHIVE_FORMAT_VERSION,
        "applied_at": datetime.now(SHANGHAI_TZ).isoformat(),
        "migrated_count": migrated_count,
        "source_bytes": source_bytes,
        "stored_bytes": stored_bytes,
        "saved_bytes": max(0, source_bytes - stored_bytes),
        "compression_ratio": (
            stored_bytes / source_bytes
            if source_bytes
            else None
        ),
        "average_file_ratio": (
            sum(ratios) / len(ratios)
            if ratios
            else None
        ),
        "before": before,
        "after": after,
    }
    path = root / "snapshot_compression_state.json"
    path.write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Compress historical LOF market snapshots as .json.gz."
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)

    result = (
        apply_snapshot_compression(args.data_root)
        if args.apply
        else plan_snapshot_compression(args.data_root)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
