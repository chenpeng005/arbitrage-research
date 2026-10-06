from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from runtime.intelligence_radar.xueqiu import (
    XueqiuAnonymousClient,
    collect_xueqiu_shadow,
    load_watchlist,
)

CHINA_TZ = ZoneInfo("Asia/Shanghai")
SCHEMA_VERSION = 1
DEFAULT_EXCERPT_CHARS = 360


def _now_china() -> datetime:
    return datetime.now(CHINA_TZ)


def _data_root() -> Path:
    configured = os.environ.get("RUNTIME_DATA_ROOT")
    if configured:
        return Path(configured)
    return Path.cwd() / "runtime_data"


def _archive_path(data_root: Path, logical_date: str) -> Path:
    year, month, _ = logical_date.split("-", 2)
    return data_root / "intelligence_radar" / "xueqiu_shadow" / year / month / f"{logical_date}.json"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _logical_dates(now: datetime, *, include_previous_date: bool = False) -> list[str]:
    today = now.astimezone(CHINA_TZ).date()
    include_previous = include_previous_date or 6 <= now.astimezone(CHINA_TZ).hour < 9
    dates = [today]
    if include_previous:
        dates.insert(0, today - timedelta(days=1))
    return [value.isoformat() for value in dates]


def _item_key(item: dict[str, Any]) -> str:
    return f"{item.get('item_type') or 'UNKNOWN'}:{item.get('item_id') or ''}"


def _archive_item(item: dict[str, Any], *, seen_at: str, excerpt_chars: int) -> dict[str, Any]:
    content = str(item.get("content") or "")
    parent = item.get("parent_context") if isinstance(item.get("parent_context"), dict) else None
    parent_excerpt = None
    if parent:
        parent_excerpt = str(parent.get("content") or "")[: min(180, excerpt_chars)] or None
    return {
        "item_type": item.get("item_type"),
        "item_id": str(item.get("item_id") or ""),
        "status_id": str(item.get("status_id") or "") or None,
        "comment_id": str(item.get("comment_id") or "") or None,
        "parent_id": str(item.get("parent_id") or "") or None,
        "author_id": str(item.get("author_id") or "") or None,
        "author_name": item.get("author_name"),
        "published_at": item.get("published_at"),
        "edited_at": item.get("edited_at"),
        "title": item.get("title") or None,
        "locator_url": item.get("locator_url"),
        "content_sha256": _sha256_text(content),
        "excerpt": content[:excerpt_chars],
        "parent_excerpt": parent_excerpt,
        "discovery_paths": list(dict.fromkeys(item.get("discovery_paths", []))),
        "watch_author_id": item.get("watch_author_id"),
        "watch_author_label": item.get("watch_author_label"),
        "first_seen_at": seen_at,
        "last_seen_at": seen_at,
        "seen_count": 1,
        "edit_count": 0,
        "prior_content_hashes": [],
    }


def _merge_item(existing: dict[str, Any], incoming: dict[str, Any], *, seen_at: str) -> tuple[dict[str, Any], bool]:
    changed = existing.get("content_sha256") != incoming.get("content_sha256")
    merged = dict(existing)
    merged["last_seen_at"] = seen_at
    merged["seen_count"] = int(existing.get("seen_count") or 0) + 1
    merged["discovery_paths"] = list(dict.fromkeys(
        list(existing.get("discovery_paths", [])) + list(incoming.get("discovery_paths", []))
    ))
    for field in ("watch_author_id", "watch_author_label"):
        if not merged.get(field) and incoming.get(field):
            merged[field] = incoming.get(field)
    if changed:
        prior = list(existing.get("prior_content_hashes", []))
        old_hash = existing.get("content_sha256")
        if old_hash and old_hash not in prior:
            prior.append(old_hash)
        merged.update({
            key: incoming.get(key)
            for key in (
                "status_id", "comment_id", "parent_id", "author_id", "author_name",
                "published_at", "edited_at", "title", "locator_url", "content_sha256",
                "excerpt", "parent_excerpt",
            )
        })
        merged["prior_content_hashes"] = prior[-5:]
        merged["edit_count"] = int(existing.get("edit_count") or 0) + 1
    return merged, changed


def _empty_archive(logical_date: str, collected_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "xueqiu",
        "mode": "SHADOW_COLLECT_ONLY",
        "logical_date": logical_date,
        "first_collected_at": collected_at,
        "last_collected_at": collected_at,
        "run_count": 0,
        "item_count": 0,
        "unique_author_count": 0,
        "item_type_counts": {},
        "discovery_path_counts": {},
        "runs": [],
        "items": [],
    }


def _load_archive(path: Path, logical_date: str, collected_at: str) -> dict[str, Any]:
    if not path.exists():
        return _empty_archive(logical_date, collected_at)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported Xueqiu shadow archive: {path}")
    return payload


def _recount(archive: dict[str, Any]) -> None:
    items = archive.get("items", [])
    item_types = Counter(str(row.get("item_type") or "UNKNOWN") for row in items)
    paths = Counter(
        str(path)
        for row in items
        for path in row.get("discovery_paths", [])
        if path
    )
    authors = {
        str(row.get("author_id"))
        for row in items
        if row.get("author_id") not in (None, "")
    }
    archive["item_count"] = len(items)
    archive["unique_author_count"] = len(authors)
    archive["item_type_counts"] = dict(item_types)
    archive["discovery_path_counts"] = dict(paths)


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def merge_collection_into_archive(
    *,
    path: Path,
    logical_date: str,
    collected_at: str,
    result: dict[str, Any],
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
    request_stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    archive = _load_archive(path, logical_date, collected_at)
    existing_by_key = {_item_key(row): row for row in archive.get("items", [])}
    new_count = 0
    changed_count = 0
    observed_count = 0

    for raw_item in result.get("items", []):
        if not isinstance(raw_item, dict):
            continue
        incoming = _archive_item(raw_item, seen_at=collected_at, excerpt_chars=excerpt_chars)
        key = _item_key(incoming)
        if not incoming.get("item_id"):
            continue
        observed_count += 1
        existing = existing_by_key.get(key)
        if existing is None:
            existing_by_key[key] = incoming
            new_count += 1
        else:
            merged, changed = _merge_item(existing, incoming, seen_at=collected_at)
            existing_by_key[key] = merged
            changed_count += int(changed)

    archive["items"] = sorted(
        existing_by_key.values(),
        key=lambda row: (str(row.get("published_at") or ""), _item_key(row)),
    )
    archive["last_collected_at"] = collected_at
    archive["run_count"] = int(archive.get("run_count") or 0) + 1
    run_record = {
        "collected_at": collected_at,
        "observed_item_count": observed_count,
        "new_item_count": new_count,
        "changed_item_count": changed_count,
        "error_count": int(result.get("error_count") or 0),
        "errors": list(result.get("errors", [])),
        "request_stats": dict(request_stats or result.get("request_stats", {})),
        "author_lane_item_count": int(result.get("author_lane", {}).get("item_count") or 0),
        "hot_pages": int(result.get("hot_exploration", {}).get("pages") or 0),
    }
    archive.setdefault("runs", []).append(run_record)
    archive["runs"] = archive["runs"][-64:]
    _recount(archive)
    _write_atomic(path, archive)
    return {
        "logical_date": logical_date,
        "archive_path": str(path),
        "observed_item_count": observed_count,
        "new_item_count": new_count,
        "changed_item_count": changed_count,
        "archive_item_count": archive["item_count"],
        "unique_author_count": archive["unique_author_count"],
        "error_count": run_record["error_count"],
    }


def _stats_snapshot(client: XueqiuAnonymousClient) -> dict[str, int]:
    stats = client.stats
    return {
        "requests": int(stats.requests),
        "bootstrap_requests": int(stats.bootstrap_requests),
        "timeline_requests": int(stats.timeline_requests),
        "comment_requests": int(stats.comment_requests),
        "detail_requests": int(stats.detail_requests),
        "hot_requests": int(stats.hot_requests),
    }


def _stats_delta(before: dict[str, int], after: dict[str, int]) -> dict[str, int]:
    return {key: int(after.get(key, 0)) - int(before.get(key, 0)) for key in after}


def run_shadow_accumulation(
    *,
    now: datetime | None = None,
    data_root: Path | None = None,
    include_previous_date: bool = False,
    hot_pages: int = 4,
    page_size: int = 20,
    excerpt_chars: int = DEFAULT_EXCERPT_CHARS,
) -> dict[str, Any]:
    now = (now or _now_china()).astimezone(CHINA_TZ)
    collected_at = now.isoformat()
    data_root = data_root or _data_root()
    authors = load_watchlist()
    client = XueqiuAnonymousClient()
    summaries: list[dict[str, Any]] = []

    for logical_date in _logical_dates(now, include_previous_date=include_previous_date):
        before = _stats_snapshot(client)
        result = collect_xueqiu_shadow(
            run_date=logical_date,
            authors=authors,
            client=client,
            author_max_pages=1,
            page_size=page_size,
            include_author_comments=False,
            hot_max_pages=hot_pages,
            include_hot=True,
        )
        after = _stats_snapshot(client)
        summary = merge_collection_into_archive(
            path=_archive_path(data_root, logical_date),
            logical_date=logical_date,
            collected_at=collected_at,
            result=result,
            excerpt_chars=excerpt_chars,
            request_stats=_stats_delta(before, after),
        )
        summaries.append(summary)

    total_errors = sum(int(row.get("error_count") or 0) for row in summaries)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": "SHADOW_ACCUMULATION_NO_AI",
        "collected_at": collected_at,
        "logical_dates": [row["logical_date"] for row in summaries],
        "watch_author_count": len(authors),
        "runs": summaries,
        "error_count": total_errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Accumulate Xueqiu Shadow observations without AI or Radar DB writes.")
    parser.add_argument("--data-root", default=None)
    parser.add_argument("--include-previous-date", action="store_true")
    parser.add_argument("--hot-pages", type=int, default=4)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--excerpt-chars", type=int, default=DEFAULT_EXCERPT_CHARS)
    args = parser.parse_args()
    result = run_shadow_accumulation(
        data_root=Path(args.data_root) if args.data_root else None,
        include_previous_date=args.include_previous_date,
        hot_pages=args.hot_pages,
        page_size=args.page_size,
        excerpt_chars=args.excerpt_chars,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["error_count"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
