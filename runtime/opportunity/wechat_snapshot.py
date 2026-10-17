from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo


CONTRACT_VERSION = "wechat-upstream-snapshot-v0.1"
SOURCE_OWNER = "CONVERTIBLE_RUNTIME"
SOURCE_KIND = "WECHAT_UPSTREAM_SNAPSHOT"
CHINA_TZ = ZoneInfo("Asia/Shanghai")


def _canonical_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=CHINA_TZ)
    return result.astimezone(CHINA_TZ)


def _event_time(item: dict[str, Any]) -> str | None:
    candidates: list[datetime] = []
    for change in item.get("changes") or []:
        if not isinstance(change, dict):
            continue
        value = _parse_time(change.get("detected_at"))
        if value is not None:
            candidates.append(value)
    created = _parse_time(item.get("created_at"))
    if created is not None:
        candidates.append(created)
    return max(candidates).isoformat() if candidates else None


def _project_item(item: dict[str, Any], *, attention_tier: str) -> dict[str, Any]:
    presentation = item.get("presentation") if isinstance(item.get("presentation"), dict) else {}
    changes = [x for x in (item.get("changes") or []) if isinstance(x, dict)]
    return {
        "bond_code": str(item.get("bond_code") or ""),
        "bond_name": str(item.get("bond_name") or ""),
        "event_time": _event_time(item),
        "attention_tier": attention_tier,
        "level": str(item.get("level") or ""),
        "presentation_kind": str(presentation.get("kind") or ""),
        "title": str(presentation.get("title") or ""),
        "summary": str(presentation.get("summary") or ""),
        "change_types": sorted({str(x.get("change_type") or "") for x in changes if x.get("change_type")}),
        "scope_ids": sorted({str(x.get("scope_id") or "") for x in changes if x.get("scope_id")}),
        "change_count": len(changes),
    }


def build_convertible_wechat_snapshot(notification_snapshot: dict[str, Any]) -> dict[str, Any]:
    captured_at = _parse_time(notification_snapshot.get("captured_at"))
    snapshot_date = str(notification_snapshot.get("snapshot_date") or "").strip()
    feed = notification_snapshot.get("feed") if isinstance(notification_snapshot.get("feed"), dict) else {}

    rows: list[dict[str, Any]] = []
    for raw in feed.get("items") or []:
        if isinstance(raw, dict):
            rows.append(_project_item(raw, attention_tier="FOCUS"))
    for raw in feed.get("other_items") or []:
        if isinstance(raw, dict):
            rows.append(_project_item(raw, attention_tier="OTHER"))
    rows.sort(key=lambda x: (str(x.get("event_time") or ""), str(x.get("bond_code") or "")), reverse=True)

    critical: list[str] = []
    if not snapshot_date:
        critical.append("logical_date")
    if captured_at is None:
        critical.append("generated_at")
    if any(not row.get("bond_code") or not row.get("bond_name") or not row.get("event_time") for row in rows):
        critical.append("row_identity_or_time")

    generated_at = captured_at.isoformat() if captured_at is not None else None
    source_id = f"convertible-notification:{snapshot_date}:{generated_at or 'UNKNOWN'}"
    return {
        "contract_version": CONTRACT_VERSION,
        "source_owner": SOURCE_OWNER,
        "source_kind": SOURCE_KIND,
        "logical_date": snapshot_date or None,
        "generated_at": generated_at,
        "content_cutoff_at": generated_at,
        "market_cutoff": notification_snapshot.get("market_cutoff"),
        "source_snapshot_id": source_id,
        "source_snapshot_sha256": sha256(_canonical_bytes(notification_snapshot)).hexdigest(),
        "source_snapshot_generated_at": generated_at,
        "source_contract_version": notification_snapshot.get("snapshot_version"),
        "status": "READY" if not critical else "BLOCKED",
        "block_reason": None if not critical else "CRITICAL_FIELDS_MISSING",
        "row_count": len(rows),
        "rows": rows,
        "delta_items": rows,
        "critical_empty_fields": critical,
    }


class ConvertibleWechatSnapshotStore:
    def __init__(self, data_root: str | Path):
        self.root = Path(data_root) / "wechat_snapshot" / "convertible"
        self.archive_dir = self.root / "archive"
        self.daily_dir = self.root / "daily"
        self.latest_path = self.root / "latest.json"

    @staticmethod
    def _write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def persist(self, payload: dict[str, Any]) -> Path:
        logical_date = str(payload.get("logical_date") or "").strip()
        generated = _parse_time(payload.get("generated_at"))
        if not logical_date or generated is None:
            raise ValueError("logical_date and generated_at are required")
        stamp = generated.strftime("%Y%m%dT%H%M%S%z")
        archive = self.archive_dir / f"{logical_date}_{stamp}.json"
        self._write(archive, payload)
        self._write(self.daily_dir / f"{logical_date}.json", payload)
        self._write(self.latest_path, payload)
        return archive

    def build_and_persist(self, source_snapshot: dict[str, Any]) -> tuple[dict[str, Any], Path]:
        payload = build_convertible_wechat_snapshot(source_snapshot)
        return payload, self.persist(payload)

    def load_latest_at_or_before(self, cutoff: str | datetime) -> dict[str, Any] | None:
        cutoff_dt = cutoff if isinstance(cutoff, datetime) else _parse_time(cutoff)
        if cutoff_dt is None:
            raise ValueError("cutoff must be an ISO datetime")
        if cutoff_dt.tzinfo is None:
            cutoff_dt = cutoff_dt.replace(tzinfo=CHINA_TZ)
        cutoff_dt = cutoff_dt.astimezone(CHINA_TZ)
        best: tuple[datetime, dict[str, Any]] | None = None
        for path in self.archive_dir.glob("*.json") if self.archive_dir.exists() else []:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                generated = _parse_time(payload.get("generated_at"))
            except Exception:
                continue
            if generated is None or generated > cutoff_dt:
                continue
            if best is None or generated > best[0]:
                best = (generated, payload)
        return best[1] if best else None
