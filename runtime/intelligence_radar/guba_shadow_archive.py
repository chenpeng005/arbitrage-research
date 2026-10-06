from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from runtime.intelligence_radar.guba import collect_guba_shadow, load_watchlist

CHINA_TZ = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = 1
DEFAULT_EXCERPT_CHARS = 360


def _now_china() -> datetime:
    return datetime.now(CHINA_TZ)


def _data_root() -> Path:
    return Path(os.environ.get("RUNTIME_DATA_ROOT", "runtime_data"))


def _archive_path(data_root: Path, logical_date: str) -> Path:
    year, month, _ = logical_date.split("-", 2)
    return data_root / "intelligence_radar" / "guba_shadow" / year / month / f"{logical_date}.json"


def _hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _item_key(row: dict[str, Any]) -> str:
    return f"{row.get('item_type')}:{row.get('item_id')}"


def _compact_item(row: dict[str, Any], *, seen_at: str, excerpt_chars: int) -> dict[str, Any]:
    text = str(row.get("content") or "")
    return {
        "item_type": row.get("item_type"),
        "item_id": str(row.get("item_id") or ""),
        "post_id": str(row.get("post_id") or "") or None,
        "parent_id": row.get("parent_id"),
        "in_reply_to_reply_id": row.get("in_reply_to_reply_id"),
        "bar_code": row.get("bar_code"),
        "bar_name": row.get("bar_name"),
        "author_id": row.get("author_id"),
        "author_name": row.get("author_name"),
        "published_at": row.get("published_at"),
        "edited_at": row.get("edited_at"),
        "title": row.get("title"),
        "locator_url": row.get("locator_url"),
        "content_hash": _hash(text),
        "excerpt": text[:excerpt_chars],
        "discovery_paths": list(row.get("discovery_paths", [])),
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
        "seen_count": 1,
        "edit_count": 0,
        "prior_content_hashes": [],
    }


def merge_collection_into_archive(
    *,
    path: Path,
    logical_date: str,
    collected_at: str,
    result: dict[str, Any],
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> dict[str, Any]:
    if path.exists():
        archive = json.loads(path.read_text(encoding="utf-8"))
    else:
        archive = {
            "schema_version": SCHEMA_VERSION,
            "source": "guba",
            "logical_date": logical_date,
            "first_collected_at": collected_at,
            "last_collected_at": collected_at,
            "run_count": 0,
            "items": [],
            "runs": [],
        }
    existing = {_item_key(row): row for row in archive.get("items", []) if isinstance(row, dict)}
    observed = new = changed = 0
    for raw in result.get("items", []):
        if not isinstance(raw, dict) or not raw.get("item_id"):
            continue
        observed += 1
        incoming = _compact_item(raw, seen_at=collected_at, excerpt_chars=excerpt_chars)
        key = _item_key(incoming)
        current = existing.get(key)
        if current is None:
            existing[key] = incoming
            new += 1
            continue
        current["last_seen_at"] = collected_at
        current["seen_count"] = int(current.get("seen_count") or 0) + 1
        current["discovery_paths"] = list(dict.fromkeys(list(current.get("discovery_paths", [])) + list(incoming.get("discovery_paths", []))))
        if current.get("content_hash") != incoming.get("content_hash"):
            previous = str(current.get("content_hash") or "")
            if previous:
                history = list(current.get("prior_content_hashes", []))
                if previous not in history:
                    history.append(previous)
                current["prior_content_hashes"] = history[-5:]
            current["content_hash"] = incoming["content_hash"]
            current["excerpt"] = incoming["excerpt"]
            current["edited_at"] = incoming.get("edited_at")
            current["edit_count"] = int(current.get("edit_count") or 0) + 1
            changed += 1
    archive["items"] = sorted(existing.values(), key=lambda row: (str(row.get("published_at") or ""), _item_key(row)))
    archive["last_collected_at"] = collected_at
    archive["run_count"] = int(archive.get("run_count") or 0) + 1
    archive["item_count"] = len(archive["items"])
    archive["unique_author_count"] = len({str(row.get("author_id") or row.get("author_name") or "") for row in archive["items"] if row.get("author_id") or row.get("author_name")})
    archive["runs"].append(
        {
            "collected_at": collected_at,
            "observed_item_count": observed,
            "new_item_count": new,
            "changed_item_count": changed,
            "error_count": int(result.get("error_count") or 0),
            "errors": list(result.get("errors", [])),
            "request_stats": dict(result.get("request_stats", {})),
        }
    )
    archive["runs"] = archive["runs"][-32:]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {
        "logical_date": logical_date,
        "archive_path": str(path),
        "observed_item_count": observed,
        "new_item_count": new,
        "changed_item_count": changed,
        "archive_item_count": archive["item_count"],
        "unique_author_count": archive["unique_author_count"],
        "error_count": int(result.get("error_count") or 0),
    }


def run_shadow_accumulation(*, run_date: str, data_root: Path | None = None) -> dict[str, Any]:
    data_root = data_root or _data_root()
    collected_at = _now_china().isoformat()
    result = collect_guba_shadow(run_date=run_date, bars=load_watchlist())
    summary = merge_collection_into_archive(
        path=_archive_path(data_root, run_date),
        logical_date=run_date,
        collected_at=collected_at,
        result=result,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "GUBA_SHADOW_NO_AI",
        "collected_at": collected_at,
        "run": summary,
        "error_count": summary["error_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Accumulate Eastmoney Guba Shadow observations without AI or Radar DB writes.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args()
    result = run_shadow_accumulation(
        run_date=args.date,
        data_root=Path(args.data_root) if args.data_root else None,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["error_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
