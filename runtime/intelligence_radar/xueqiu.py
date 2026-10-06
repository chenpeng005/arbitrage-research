from __future__ import annotations

import argparse
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import requests

BASE_URL = "https://www.xueqiu.com"
DEFAULT_WATCHLIST_PATH = Path(__file__).with_name("xueqiu_watchlist.json")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/154 Safari/537.36"
)
CHINA_TZ = ZoneInfo("Asia/Shanghai")


class XueqiuAccessError(RuntimeError):
    pass


@dataclass
class XueqiuRequestStats:
    requests: int = 0
    bootstrap_requests: int = 0
    timeline_requests: int = 0
    comment_requests: int = 0
    detail_requests: int = 0
    hot_requests: int = 0


def _clean_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<img\b[^>]*alt=[\"']([^\"']*)[\"'][^>]*>", r"\1", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _millis_to_china(value: Any) -> str | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        seconds = int(value) / 1000
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(seconds, tz=CHINA_TZ).isoformat()


def _date_of_millis(value: Any) -> str | None:
    timestamp = _millis_to_china(value)
    return timestamp[:10] if timestamp else None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def classify_status(row: dict[str, Any]) -> str:
    """Classify one user-timeline row without relying on display-only type codes."""
    comment_id = _safe_int(row.get("commentId"))
    parent_status_id = _safe_int(row.get("retweet_status_id"))
    if comment_id and parent_status_id:
        return "REPLY"
    if parent_status_id:
        return "REPOST"
    return "POST"


def _user_name(row: dict[str, Any]) -> str | None:
    user = row.get("user")
    if isinstance(user, dict):
        name = str(user.get("screen_name") or "").strip()
        return name or None
    return None


def normalize_status(row: dict[str, Any]) -> dict[str, Any]:
    item_type = classify_status(row)
    status_id = str(row.get("id") or "").strip()
    comment_id = str(row.get("commentId") or "").strip()
    item_id = comment_id if item_type == "REPLY" and comment_id not in {"", "0"} else status_id
    parent_id = str(row.get("retweet_status_id") or "").strip()
    if parent_id == "0":
        parent_id = ""
    target = str(row.get("target") or "").strip()
    locator_url = BASE_URL + target if target.startswith("/") else (target or None)
    content = _clean_html(row.get("description") or row.get("text"))
    parent = row.get("retweeted_status") if isinstance(row.get("retweeted_status"), dict) else None
    parent_context = None
    if parent:
        parent_context = {
            "status_id": str(parent.get("id") or ""),
            "author_id": str(parent.get("user_id") or "") or None,
            "author_name": _user_name(parent),
            "published_at": _millis_to_china(parent.get("created_at")),
            "edited_at": _millis_to_china(parent.get("edited_at")),
            "content": _clean_html(parent.get("description") or parent.get("text")),
            "target": parent.get("target"),
        }
    return {
        "source": "xueqiu",
        "item_type": item_type,
        "item_id": item_id,
        "status_id": status_id,
        "comment_id": comment_id if comment_id not in {"", "0"} else None,
        "parent_id": parent_id or None,
        "author_id": str(row.get("user_id") or "") or None,
        "author_name": _user_name(row),
        "published_at": _millis_to_china(row.get("created_at")),
        "edited_at": _millis_to_china(row.get("edited_at")),
        "content": content,
        "title": _clean_html(row.get("title")),
        "locator_url": locator_url,
        "reply_count": _safe_int(row.get("reply_count")),
        "like_count": _safe_int(row.get("like_count")),
        "parent_context": parent_context,
    }


def normalize_comment(row: dict[str, Any], *, fallback_status_id: str) -> dict[str, Any]:
    comment_id = str(row.get("id") or "").strip()
    status_id = str(row.get("statusId") or row.get("status_id") or fallback_status_id).strip()
    root_status_id = str(row.get("root_in_reply_to_status_id") or status_id).strip()
    in_reply_to_comment_id = str(row.get("in_reply_to_comment_id") or "").strip()
    return {
        "source": "xueqiu",
        "item_type": "COMMENT",
        "item_id": comment_id,
        "status_id": status_id or fallback_status_id,
        "parent_id": root_status_id or fallback_status_id,
        "in_reply_to_comment_id": in_reply_to_comment_id if in_reply_to_comment_id not in {"", "0"} else None,
        "author_id": str(row.get("user_id") or "") or None,
        "author_name": _user_name(row),
        "published_at": _millis_to_china(row.get("created_at")),
        "edited_at": _millis_to_china(row.get("edited_at")),
        "content": _clean_html(row.get("description") or row.get("text")),
        "locator_url": None,
        "reply_count": _safe_int(row.get("reply_count")),
        "like_count": _safe_int(row.get("like_count")),
    }


class XueqiuAnonymousClient:
    """Small public-reader client. It never attempts to solve or bypass WAF challenges."""

    def __init__(self, *, timeout_seconds: int = 15, sleep_seconds: float = 0.35, session=None):
        self.timeout_seconds = timeout_seconds
        self.sleep_seconds = max(0.0, float(sleep_seconds))
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": BASE_URL + "/",
            }
        )
        self.stats = XueqiuRequestStats()
        self._bootstrapped = False

    @staticmethod
    def _looks_like_waf(text: str) -> bool:
        head = text[:8000]
        return "aliyun_waf" in head or "_waf_" in head

    def bootstrap(self) -> None:
        response = self.session.get(BASE_URL + "/", timeout=self.timeout_seconds)
        self.stats.requests += 1
        self.stats.bootstrap_requests += 1
        if response.status_code != 200:
            raise XueqiuAccessError(f"bootstrap HTTP {response.status_code}")
        if self._looks_like_waf(response.text):
            raise XueqiuAccessError("bootstrap returned WAF challenge instead of public page")
        self._bootstrapped = True

    def _get_json(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self._bootstrapped:
            self.bootstrap()
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        response = self.session.get(BASE_URL + path, params=params, timeout=self.timeout_seconds)
        self.stats.requests += 1
        if response.status_code in {401, 403}:
            self._bootstrapped = False
            self.bootstrap()
            if self.sleep_seconds:
                time.sleep(self.sleep_seconds)
            response = self.session.get(BASE_URL + path, params=params, timeout=self.timeout_seconds)
            self.stats.requests += 1
        if response.status_code != 200:
            raise XueqiuAccessError(f"GET {path} HTTP {response.status_code}")
        content_type = str(response.headers.get("content-type") or "").lower()
        if "json" not in content_type:
            if self._looks_like_waf(response.text):
                raise XueqiuAccessError(f"GET {path} returned WAF challenge")
            raise XueqiuAccessError(f"GET {path} returned non-JSON content")
        try:
            payload = response.json()
        except ValueError as exc:
            raise XueqiuAccessError(f"GET {path} returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise XueqiuAccessError(f"GET {path} returned unexpected JSON type")
        return payload

    def user_timeline(self, user_id: str, *, page: int = 1, count: int = 20) -> dict[str, Any]:
        self.stats.timeline_requests += 1
        return self._get_json(
            "/statuses/user_timeline.json",
            {"user_id": str(user_id), "page": int(page), "count": int(count)},
        )

    def status_comments(self, status_id: str, *, page: int = 1, count: int = 20) -> dict[str, Any]:
        self.stats.comment_requests += 1
        return self._get_json(
            "/statuses/comments.json",
            {"id": str(status_id), "page": int(page), "count": int(count), "sort": "time"},
        )

    def status_detail(self, status_id: str) -> dict[str, Any]:
        self.stats.detail_requests += 1
        return self._get_json("/statuses/show.json", {"id": str(status_id)})

    def hot_list(self, *, max_id: int = -1, size: int = 10) -> dict[str, Any]:
        self.stats.hot_requests += 1
        return self._get_json(
            "/statuses/hot/listV2.json",
            {"since_id": -1, "max_id": int(max_id), "size": int(size)},
        )


def hydrate_status_if_needed(
    client: XueqiuAnonymousClient,
    row: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    """Fetch the public detail only when timeline/hot payload says the body is truncated."""
    if not bool(row.get("truncated")):
        return row, None
    status_id = str(row.get("id") or "").strip()
    if not status_id:
        return row, "truncated status has no id"
    try:
        detail = client.status_detail(status_id)
    except Exception as exc:
        return row, f"detail {status_id}: {type(exc).__name__}: {exc}"
    if str(detail.get("id") or "") != status_id:
        return row, f"detail {status_id}: id mismatch"
    return detail, None


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    authors = payload.get("authors", []) if isinstance(payload, dict) else []
    rows: list[dict[str, str]] = []
    for row in authors:
        if not isinstance(row, dict):
            continue
        user_id = str(row.get("user_id") or "").strip()
        if not user_id:
            continue
        rows.append({"user_id": user_id, "label": str(row.get("label") or user_id).strip()})
    return rows


def _dedupe_items(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row.get("item_type") or ""), str(row.get("item_id") or ""))
        if not key[1]:
            continue
        if key not in merged:
            merged[key] = dict(row)
            merged[key]["discovery_paths"] = list(row.get("discovery_paths", []))
            order.append(key)
            continue
        current = merged[key]
        current["discovery_paths"] = list(dict.fromkeys(
            list(current.get("discovery_paths", [])) + list(row.get("discovery_paths", []))
        ))
        for field in ("watch_author_id", "watch_author_label"):
            if not current.get(field) and row.get(field):
                current[field] = row.get(field)
    return [merged[key] for key in order]


def collect_hot_exploration(
    *,
    run_date: str,
    client: XueqiuAnonymousClient,
    max_pages: int = 2,
    page_size: int = 10,
) -> dict[str, Any]:
    """Bounded non-keyword discovery lane based on Xueqiu's public hot feed.

    This lane is intentionally labeled exploration: platform heat/ranking biases coverage,
    so it must never be interpreted as a complete site-wide feed.
    """
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    cursor = -1
    pages = 0
    for _ in range(max(1, int(max_pages))):
        try:
            payload = client.hot_list(max_id=cursor, size=page_size)
        except Exception as exc:
            errors.append(f"hot: {type(exc).__name__}: {exc}")
            break
        pages += 1
        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list) or not raw_items:
            break
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            status = raw.get("original_status")
            if not isinstance(status, dict):
                data = raw.get("data")
                status = data if isinstance(data, dict) and data.get("id") else None
            if not isinstance(status, dict):
                continue
            if _date_of_millis(status.get("created_at")) != run_date:
                continue
            hydrated, detail_error = hydrate_status_if_needed(client, status)
            if detail_error:
                errors.append(detail_error)
            row = normalize_status(hydrated)
            row["discovery_paths"] = ["XUEQIU_HOT_EXPLORATION"]
            items.append(row)
        next_cursor = payload.get("next_max_id")
        try:
            next_cursor = int(next_cursor)
        except (TypeError, ValueError):
            break
        if next_cursor == cursor or next_cursor < 0:
            break
        cursor = next_cursor
    return {
        "items": _dedupe_items(items),
        "pages": pages,
        "error_count": len(errors),
        "errors": errors,
        "coverage_note": "public hot feed is discovery-biased and is not full-site coverage",
    }


def build_increment_candidates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map normalized Xueqiu items into the existing source-agnostic Ledger candidate shape.

    `question_id` is a legacy field name in the Broad/Ledger pipeline; for Xueqiu it
    carries the stable thread/root status id. This function does not call AI.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        thread_id = str(item.get("parent_id") or item.get("status_id") or item.get("item_id") or "").strip()
        if not thread_id:
            continue
        grouped.setdefault(thread_id, []).append(item)

    candidates: list[dict[str, Any]] = []
    for thread_id, rows in grouped.items():
        rows = sorted(rows, key=lambda row: str(row.get("published_at") or ""))
        contexts: list[dict[str, Any]] = []
        daily_segments: list[dict[str, Any]] = []
        seen_context_ids: set[str] = set()
        thread_url = None
        thread_author = None
        thread_published_at = None
        title = ""

        for row in rows:
            parent = row.get("parent_context") if isinstance(row.get("parent_context"), dict) else None
            if parent:
                parent_status_id = str(parent.get("status_id") or thread_id)
                if parent_status_id and parent_status_id not in seen_context_ids:
                    parent_target = str(parent.get("target") or "")
                    parent_url = BASE_URL + parent_target if parent_target.startswith("/") else None
                    contexts.append(
                        {
                            "segment_id": f"xq_status_{parent_status_id}",
                            "kind": "XQ_POST",
                            "locator_id": parent_status_id,
                            "locator_url": parent_url,
                            "author": parent.get("author_name"),
                            "published_at": parent.get("published_at"),
                            "text": str(parent.get("content") or ""),
                            "is_daily": False,
                        }
                    )
                    seen_context_ids.add(parent_status_id)
                    thread_url = thread_url or parent_url
                    thread_author = thread_author or parent.get("author_name")
                    thread_published_at = thread_published_at or parent.get("published_at")
            item_type = str(row.get("item_type") or "ITEM")
            item_id = str(row.get("item_id") or "")
            if not item_id:
                continue
            daily_segments.append(
                {
                    "segment_id": f"xq_{item_type.lower()}_{item_id}",
                    "kind": f"XQ_{item_type}",
                    "locator_id": item_id,
                    "locator_url": row.get("locator_url"),
                    "author": row.get("author_name"),
                    "published_at": row.get("published_at"),
                    "text": str(row.get("content") or ""),
                    "is_daily": True,
                }
            )
            if item_type == "POST":
                thread_url = thread_url or row.get("locator_url")
                thread_author = thread_author or row.get("author_name")
                thread_published_at = thread_published_at or row.get("published_at")
                title = title or str(row.get("title") or "")

        if not daily_segments:
            continue
        activity_at = max(
            (str(seg.get("published_at") or "") for seg in daily_segments),
            default="",
        ) or None
        if not thread_url:
            thread_url = f"{BASE_URL}/status/{thread_id}"
        if not title:
            title = f"雪球讨论 {thread_id}"
        discovery_paths = list(dict.fromkeys(
            path
            for row in rows
            for path in row.get("discovery_paths", [])
        ))
        candidates.append(
            {
                "question_id": thread_id,
                "title": title,
                "url": thread_url,
                "category": "xueqiu",
                "question_author": thread_author,
                "question_published_at": thread_published_at,
                "activity_at": activity_at,
                "context_segments": contexts,
                "daily_segments": daily_segments,
                "discovery_paths": discovery_paths,
            }
        )
    return candidates


def collect_author_shadow(
    *,
    run_date: str,
    authors: list[dict[str, str]],
    client: XueqiuAnonymousClient | None = None,
    max_pages: int = 2,
    page_size: int = 20,
    include_comments: bool = False,
    max_comment_threads: int = 5,
    max_comment_pages: int = 1,
) -> dict[str, Any]:
    client = client or XueqiuAnonymousClient()
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    author_meta: list[dict[str, Any]] = []
    comment_threads_used = 0

    for author in authors:
        user_id = str(author["user_id"])
        label = str(author.get("label") or user_id)
        timeline_rows: list[dict[str, Any]] = []
        pages_read = 0
        try:
            for page in range(1, max(1, int(max_pages)) + 1):
                payload = client.user_timeline(user_id, page=page, count=page_size)
                pages_read += 1
                statuses = payload.get("statuses", [])
                if not isinstance(statuses, list) or not statuses:
                    break
                timeline_rows.extend(row for row in statuses if isinstance(row, dict))
                dates = [_date_of_millis(row.get("created_at")) for row in statuses if isinstance(row, dict)]
                dated = [value for value in dates if value]
                if dated and min(dated) < run_date:
                    break
        except Exception as exc:
            errors.append(f"timeline {label}/{user_id}: {type(exc).__name__}: {exc}")
            author_meta.append({"user_id": user_id, "label": label, "pages": pages_read, "error": str(exc)})
            continue

        daily_statuses = [row for row in timeline_rows if _date_of_millis(row.get("created_at")) == run_date]
        hydrated_daily: list[dict[str, Any]] = []
        for row in daily_statuses:
            hydrated, detail_error = hydrate_status_if_needed(client, row)
            if detail_error:
                errors.append(detail_error)
            hydrated_daily.append(hydrated)
        normalized = [normalize_status(row) for row in hydrated_daily]
        for row in normalized:
            row["watch_author_id"] = user_id
            row["watch_author_label"] = label
            row["discovery_paths"] = ["XUEQIU_AUTHOR_SHADOW"]
        items.extend(normalized)

        comments_added = 0
        if include_comments and comment_threads_used < max_comment_threads:
            for status in timeline_rows:
                if comment_threads_used >= max_comment_threads:
                    break
                if classify_status(status) != "POST" or _safe_int(status.get("reply_count")) <= 0:
                    continue
                status_id = str(status.get("id") or "")
                if not status_id:
                    continue
                comment_threads_used += 1
                try:
                    for page in range(1, max(1, int(max_comment_pages)) + 1):
                        payload = client.status_comments(status_id, page=page, count=page_size)
                        comments = payload.get("comments", [])
                        if not isinstance(comments, list) or not comments:
                            break
                        for comment in comments:
                            if not isinstance(comment, dict) or _date_of_millis(comment.get("created_at")) != run_date:
                                continue
                            row = normalize_comment(comment, fallback_status_id=status_id)
                            row["watch_author_id"] = user_id
                            row["watch_author_label"] = label
                            row["discovery_paths"] = ["XUEQIU_AUTHOR_SHADOW"]
                            items.append(row)
                            comments_added += 1
                        if page >= _safe_int(payload.get("maxPage")):
                            break
                except Exception as exc:
                    errors.append(f"comments {status_id}: {type(exc).__name__}: {exc}")

        author_meta.append(
            {
                "user_id": user_id,
                "label": label,
                "pages": pages_read,
                "timeline_rows": len(timeline_rows),
                "daily_statuses": len(daily_statuses),
                "comments_added": comments_added,
            }
        )

    items = _dedupe_items(items)
    counts: dict[str, int] = {}
    for row in items:
        item_type = str(row.get("item_type") or "UNKNOWN")
        counts[item_type] = counts.get(item_type, 0) + 1
    return {
        "schema_version": 1,
        "source": "xueqiu",
        "run_date": run_date,
        "mode": "SHADOW_COLLECT_ONLY",
        "author_count": len(authors),
        "item_count": len(items),
        "item_type_counts": counts,
        "authors": author_meta,
        "request_stats": {
            "requests": client.stats.requests,
            "bootstrap_requests": client.stats.bootstrap_requests,
            "timeline_requests": client.stats.timeline_requests,
            "comment_requests": client.stats.comment_requests,
            "detail_requests": client.stats.detail_requests,
            "hot_requests": client.stats.hot_requests,
        },
        "error_count": len(errors),
        "errors": errors,
        "items": items,
    }


def collect_xueqiu_shadow(
    *,
    run_date: str,
    authors: list[dict[str, str]],
    client: XueqiuAnonymousClient | None = None,
    author_max_pages: int = 2,
    page_size: int = 20,
    include_author_comments: bool = False,
    max_comment_threads: int = 5,
    hot_max_pages: int = 2,
    include_hot: bool = True,
) -> dict[str, Any]:
    client = client or XueqiuAnonymousClient()
    author_result = collect_author_shadow(
        run_date=run_date,
        authors=authors,
        client=client,
        max_pages=author_max_pages,
        page_size=page_size,
        include_comments=include_author_comments,
        max_comment_threads=max_comment_threads,
    )
    hot_result = (
        collect_hot_exploration(
            run_date=run_date,
            client=client,
            max_pages=hot_max_pages,
            page_size=min(page_size, 20),
        )
        if include_hot
        else {"items": [], "pages": 0, "error_count": 0, "errors": [], "coverage_note": None}
    )
    items = _dedupe_items(list(author_result.get("items", [])) + list(hot_result.get("items", [])))
    counts: dict[str, int] = {}
    path_counts: dict[str, int] = {}
    for row in items:
        item_type = str(row.get("item_type") or "UNKNOWN")
        counts[item_type] = counts.get(item_type, 0) + 1
        for path in row.get("discovery_paths", []):
            path_counts[path] = path_counts.get(path, 0) + 1
    candidates = build_increment_candidates(items)
    return {
        "schema_version": 1,
        "source": "xueqiu",
        "run_date": run_date,
        "mode": "SHADOW_COLLECT_ONLY",
        "item_count": len(items),
        "item_type_counts": counts,
        "discovery_path_counts": path_counts,
        "candidate_count": len(candidates),
        "author_lane": {key: value for key, value in author_result.items() if key != "items"},
        "hot_exploration": {key: value for key, value in hot_result.items() if key != "items"},
        "request_stats": {
            "requests": client.stats.requests,
            "bootstrap_requests": client.stats.bootstrap_requests,
            "timeline_requests": client.stats.timeline_requests,
            "comment_requests": client.stats.comment_requests,
            "detail_requests": client.stats.detail_requests,
            "hot_requests": client.stats.hot_requests,
        },
        "error_count": int(author_result.get("error_count") or 0) + int(hot_result.get("error_count") or 0),
        "errors": list(author_result.get("errors", [])) + list(hot_result.get("errors", [])),
        "items": items,
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Xueqiu public Shadow collector; no AI and no DB writes.")
    parser.add_argument("--date", required=True, help="Asia/Shanghai date, YYYY-MM-DD")
    parser.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST_PATH))
    parser.add_argument("--user-id", action="append", default=[], help="Temporary probe user id; repeatable")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--page-size", type=int, default=20)
    parser.add_argument("--include-comments", action="store_true")
    parser.add_argument("--max-comment-threads", type=int, default=5)
    parser.add_argument("--hot-pages", type=int, default=2)
    parser.add_argument("--no-hot", action="store_true", help="Disable public hot exploration lane")
    parser.add_argument("--show-items", action="store_true")
    args = parser.parse_args()

    authors = load_watchlist(Path(args.watchlist))
    known_ids = {row["user_id"] for row in authors}
    for user_id in args.user_id:
        user_id = str(user_id).strip()
        if user_id and user_id not in known_ids:
            authors.append({"user_id": user_id, "label": f"probe:{user_id}"})
            known_ids.add(user_id)

    result = collect_xueqiu_shadow(
        run_date=args.date,
        authors=authors,
        author_max_pages=args.max_pages,
        page_size=args.page_size,
        include_author_comments=args.include_comments,
        max_comment_threads=args.max_comment_threads,
        hot_max_pages=args.hot_pages,
        include_hot=not args.no_hot,
    )
    if not args.show_items:
        result = {key: value for key, value in result.items() if key not in {"items", "candidates"}}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
