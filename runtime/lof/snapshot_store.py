from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from typing import Any


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def snapshot_to_json(snapshot: dict) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def snapshot_from_json(text: str) -> dict:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("snapshot payload must be an object")
    return value


class LofSnapshotStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.snapshots_dir = self.root / "snapshots"
        self.latest_path = self.root / "latest_market_snapshot.json"

    def persist(self, snapshot: dict) -> Path:
        snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
        if not snapshot_id:
            raise ValueError("snapshot_id is required")

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)

        payload = snapshot_to_json(snapshot) + "\n"
        archive_path = self.snapshots_dir / f"{snapshot_id}.json"
        self._atomic_write(archive_path, payload)
        self._atomic_write(self.latest_path, payload)
        return archive_path

    def load_latest(self) -> dict | None:
        if not self.latest_path.exists():
            return None
        return snapshot_from_json(
            self.latest_path.read_text(encoding="utf-8")
        )

    @staticmethod
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
