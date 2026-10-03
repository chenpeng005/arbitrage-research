from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
import tempfile
from typing import Iterable


ARCHIVE_FORMAT_VERSION = "LOF_SNAPSHOT_ARCHIVE_GZIP_V1"
GZIP_LEVEL = 6


def snapshot_id_from_path(path: str | Path) -> str:
    name = Path(path).name
    if name.endswith(".json.gz"):
        return name[:-8]
    if name.endswith(".json"):
        return name[:-5]
    return Path(path).stem


def iter_snapshot_paths(data_root: str | Path) -> list[Path]:
    directory = Path(data_root) / "snapshots"
    by_id: dict[str, Path] = {}

    # Raw files are supported for backward compatibility during migration.
    for path in directory.glob("runtime-*.json"):
        by_id[snapshot_id_from_path(path)] = path

    # Prefer compressed files if both forms exist transiently.
    for path in directory.glob("runtime-*.json.gz"):
        by_id[snapshot_id_from_path(path)] = path

    return [
        by_id[key]
        for key in sorted(by_id)
    ]


def read_snapshot_text(path: str | Path) -> str:
    path = Path(path)
    if path.name.endswith(".json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return handle.read()
    return path.read_text(encoding="utf-8")


def read_snapshot_json(path: str | Path) -> dict:
    value = json.loads(read_snapshot_text(path))
    if not isinstance(value, dict):
        raise ValueError("snapshot payload must be an object")
    return value


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def write_snapshot_gzip(path: str | Path, text: str) -> Path:
    path = Path(path)
    if not path.name.endswith(".json.gz"):
        raise ValueError("compressed snapshot path must end with .json.gz")
    raw = text.encode("utf-8")
    payload = gzip.compress(
        raw,
        compresslevel=GZIP_LEVEL,
        mtime=0,
    )
    _atomic_write_bytes(path, payload)
    return path


def compressed_path_for(raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if path.name.endswith(".json.gz"):
        return path
    if not path.name.endswith(".json"):
        raise ValueError("raw snapshot path must end with .json")
    return Path(str(path) + ".gz")


def compress_existing_snapshot(path: str | Path) -> dict:
    path = Path(path)
    if path.name.endswith(".json.gz"):
        return {
            "path": str(path),
            "status": "ALREADY_COMPRESSED",
            "raw_bytes": None,
            "compressed_bytes": path.stat().st_size,
        }
    if not path.name.endswith(".json"):
        raise ValueError(f"unsupported snapshot path: {path}")

    raw = path.read_bytes()
    # Validate before replacing anything.
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("snapshot payload must be an object")

    target = compressed_path_for(path)
    payload = gzip.compress(
        raw,
        compresslevel=GZIP_LEVEL,
        mtime=0,
    )

    # Verify round-trip before removing the raw source.
    if gzip.decompress(payload) != raw:
        raise ValueError(f"gzip round-trip verification failed: {path}")

    _atomic_write_bytes(target, payload)
    # Verify the file that was actually written.
    if gzip.open(target, "rb").read() != raw:
        target.unlink(missing_ok=True)
        raise ValueError(f"written gzip verification failed: {target}")

    raw_bytes = len(raw)
    compressed_bytes = len(payload)
    path.unlink()

    return {
        "path": str(target),
        "status": "COMPRESSED",
        "raw_bytes": raw_bytes,
        "compressed_bytes": compressed_bytes,
        "ratio": (
            compressed_bytes / raw_bytes
            if raw_bytes
            else None
        ),
    }


def snapshot_archive_stats(data_root: str | Path) -> dict:
    paths = iter_snapshot_paths(data_root)
    raw_count = 0
    compressed_count = 0
    stored_bytes = 0
    for path in paths:
        stored_bytes += path.stat().st_size
        if path.name.endswith(".json.gz"):
            compressed_count += 1
        else:
            raw_count += 1
    return {
        "archive_format_version": ARCHIVE_FORMAT_VERSION,
        "snapshot_count": len(paths),
        "raw_count": raw_count,
        "compressed_count": compressed_count,
        "stored_bytes": stored_bytes,
    }
