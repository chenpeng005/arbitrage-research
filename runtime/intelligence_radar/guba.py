from __future__ import annotations

import argparse
import hashlib
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
from bs4 import BeautifulSoup

BASE_URL = "https://guba.eastmoney.com"
LIST_API = "https://gbapi.eastmoney.com/webarticlelist/api/Article/Articlelist"
REPLY_API = "https://gbapi.eastmoney.com/reply/JSONP/ArticleNewReplyList"
DEFAULT_WATCHLIST_PATH = Path(__file__).with_name("guba_watchlist.json")
CHINA_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/154 Safari/537.36"
)


class GubaAccessError(RuntimeError):
    pass


class GubaAccessChallenge(GubaAccessError):
    pass


@dataclass
class GubaRequestStats:
    requests: int = 0
    index_requests: int = 0
    bar_requests: int = 0
    detail_requests: int = 0
    reply_requests: int = 0


def _clean_html(value: Any) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _content_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _date_of(value: Any) -> str | None:
    text = str(value or "").strip()
    if len(text) >= 10 and re.match(r"^\d{4}-\d{2}-\d{2}", text):
        return text[:10]
    return None


def _parse_mmdd_time(value: str, *, year: int) -> str | None:
    text = str(value or "").strip()
    try:
        return datetime.strptime(f"{year}-{text}", "%Y-%m-%d %H:%M").replace(tzinfo=CHINA_TZ).isoformat()
    except ValueError:
        return None


def _json_after_marker(text: str, marker: str) -> dict[str, Any]:
    pos = text.find(marker)
    if pos < 0:
        raise GubaAccessError(f"marker not found: {marker}")
    start = text.find("{", pos + len(marker))
    if start < 0:
        raise GubaAccessError(f"JSON object not found after: {marker}")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[start:])
    except ValueError as exc:
        raise GubaAccessError(f"invalid JSON after marker: {marker}") from exc
    if not isinstance(value, dict):
        raise GubaAccessError(f"unexpected JSON type after marker: {marker}")
    return value


def _parse_jsonp(text: str) -> dict[str, Any]:
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        raise GubaAccessError("invalid JSONP reply payload")
    try:
        payload = json.loads(text[start + 1 : end])
    except ValueError as exc:
        raise GubaAccessError("invalid JSONP reply JSON") from exc
    if not isinstance(payload, dict):
        raise GubaAccessError("unexpected reply payload type")
    return payload


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def load_watchlist(path: Path = DEFAULT_WATCHLIST_PATH) -> list[dict[str, str]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    for row in payload.get("bars", []) if isinstance(payload, dict) else []:
        if not isinstance(row, dict):
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        rows.append(
            {
                "code": code,
                "name": str(row.get("name") or code).strip(),
                "reason": str(row.get("reason") or "").strip(),
            }
        )
    return rows


class GubaPublicClient:
    def __init__(self, *, timeout_seconds: int = 15, sleep_seconds: float = 0.25, session=None):
        self.timeout_seconds = timeout_seconds
        self.sleep_seconds = max(0.0, float(sleep_seconds))
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": DEFAULT_USER_AGENT,
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Referer": BASE_URL + "/index.html",
            }
        )
        self.stats = GubaRequestStats()

    def _get(self, url: str, *, params: dict[str, Any] | None = None) -> requests.Response:
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        response = self.session.get(url, params=params, timeout=self.timeout_seconds)
        self.stats.requests += 1
        if response.status_code != 200:
            raise GubaAccessError(f"GET {url} HTTP {response.status_code}")
        if "text/html" in str(response.headers.get("content-type") or "").lower():
            response.encoding = "utf-8"
            if "身份核实" in response.text[:8000]:
                raise GubaAccessChallenge("Eastmoney identity-verification challenge")
        return response

    def index_html(self) -> str:
        self.stats.index_requests += 1
        response = self._get(BASE_URL + "/index.html")
        response.encoding = "utf-8"
        return response.text

    def bar_page(self, code: str, *, page: int = 1) -> dict[str, Any]:
        self.stats.bar_requests += 1
        response = self._get(
            LIST_API,
            params={
                "code": str(code),
                "type": 0,
                "index": int(page),
                "pageSize": 20,
                "deviceid": "100",
                "version": "200",
                "product": "Guba",
                "plat": "Web",
            },
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise GubaAccessError("invalid Guba bar-list JSON") from exc
        if not isinstance(payload, dict):
            raise GubaAccessError("unexpected Guba bar-list JSON type")
        return payload

    def post_detail(self, code: str, post_id: str) -> dict[str, Any]:
        self.stats.detail_requests += 1
        response = self._get(BASE_URL + f"/news,{code},{post_id}.html")
        response.encoding = "utf-8"
        return _json_after_marker(response.text, "var post_article=")

    def post_replies(self, post_id: str, *, page: int = 1, page_size: int = 50) -> dict[str, Any]:
        self.stats.reply_requests += 1
        callback = "gubaRadarCb"
        response = self._get(
            REPLY_API,
            params={
                "callback": callback,
                "plat": "web",
                "version": "300",
                "product": "guba",
                "postid": str(post_id),
                "sort": "1",
                "sorttype": "1",
                "p": int(page),
                "ps": int(page_size),
                "type": "0",
                "_": int(time.time() * 1000),
            },
        )
        response.encoding = "utf-8"
        return _parse_jsonp(response.text)


def normalize_post(detail: dict[str, Any], *, discovery_path: str) -> dict[str, Any]:
    guba = detail.get("post_guba") if isinstance(detail.get("post_guba"), dict) else {}
    user = detail.get("post_user") if isinstance(detail.get("post_user"), dict) else {}
    post_id = str(detail.get("post_id") or "").strip()
    code = str(guba.get("stockbar_code") or "").strip()
    return {
        "source": "guba",
        "item_type": "POST",
        "item_id": post_id,
        "post_id": post_id,
        "parent_id": None,
        "bar_code": code or None,
        "bar_name": str(guba.get("stockbar_name") or "").strip() or None,
        "author_id": str(user.get("user_id") or "").strip() or None,
        "author_name": str(user.get("user_nickname") or "").strip() or None,
        "published_at": str(detail.get("post_publish_time") or "").strip() or None,
        "edited_at": str(detail.get("post_last_time") or "").strip() or None,
        "title": str(detail.get("post_title") or "").strip(),
        "content": _clean_html(detail.get("post_content") or detail.get("post_abstract")),
        "locator_url": BASE_URL + f"/news,{code},{post_id}.html" if code and post_id else None,
        "reply_count": _safe_int(detail.get("post_comment_count")),
        "like_count": _safe_int(detail.get("post_like_count")),
        "discovery_paths": [discovery_path],
    }


def normalize_post_from_list(row: dict[str, Any], *, discovery_path: str) -> dict[str, Any]:
    post_id = str(row.get("post_id") or "").strip()
    code = str(row.get("stockbar_code") or "").strip()
    title = str(row.get("post_title") or "").strip()
    return {
        "source": "guba",
        "item_type": "POST",
        "item_id": post_id,
        "post_id": post_id,
        "parent_id": None,
        "bar_code": code or None,
        "bar_name": str(row.get("stockbar_name") or "").removesuffix("吧").strip() or None,
        "author_id": str(row.get("user_id") or "").strip() or None,
        "author_name": str(row.get("user_nickname") or "").strip() or None,
        "published_at": str(row.get("post_publish_time") or "").strip() or None,
        "edited_at": str(row.get("post_last_time") or "").strip() or None,
        "title": title,
        "content": title,
        "locator_url": BASE_URL + f"/news,{code},{post_id}.html" if code and post_id else None,
        "reply_count": _safe_int(row.get("post_comment_count")),
        "like_count": 0,
        "discovery_paths": [discovery_path],
    }


def _reply_user_name(row: dict[str, Any]) -> str | None:
    user = row.get("reply_user") if isinstance(row.get("reply_user"), dict) else {}
    return str(user.get("user_nickname") or row.get("reply_user_nickname") or "").strip() or None


def normalize_reply(row: dict[str, Any], *, code: str, post_title: str, discovery_path: str, parent_reply_id: str | None = None) -> dict[str, Any]:
    reply_id = str(row.get("reply_id") or "").strip()
    post_id = str(row.get("source_post_id") or "").strip()
    source_reply = row.get("source_reply") if isinstance(row.get("source_reply"), list) else []
    referenced_reply_id = None
    if source_reply and isinstance(source_reply[0], dict):
        referenced_reply_id = str(source_reply[0].get("source_reply_id") or "").strip() or None
    return {
        "source": "guba",
        "item_type": "REPLY",
        "item_id": reply_id,
        "post_id": post_id,
        "parent_id": post_id or None,
        "in_reply_to_reply_id": referenced_reply_id or parent_reply_id,
        "bar_code": code,
        "bar_name": None,
        "author_id": str(row.get("user_id") or "").strip() or None,
        "author_name": _reply_user_name(row),
        "published_at": str(row.get("reply_publish_time") or row.get("reply_time") or "").strip() or None,
        "edited_at": None,
        "title": post_title,
        "content": _clean_html(row.get("reply_text")),
        "locator_url": BASE_URL + f"/news,{code},{post_id}.html" if code and post_id else None,
        "reply_count": _safe_int(row.get("reply_count")),
        "like_count": _safe_int(row.get("reply_like_count")),
        "discovery_paths": [discovery_path],
    }


def flatten_replies(payload: dict[str, Any], *, code: str, post_title: str, discovery_path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for top in payload.get("re", []) if isinstance(payload.get("re"), list) else []:
        if not isinstance(top, dict):
            continue
        rows.append(normalize_reply(top, code=code, post_title=post_title, discovery_path=discovery_path))
        top_id = str(top.get("reply_id") or "").strip() or None
        for child in top.get("child_replys", []) if isinstance(top.get("child_replys"), list) else []:
            if isinstance(child, dict):
                rows.append(
                    normalize_reply(
                        child,
                        code=code,
                        post_title=post_title,
                        discovery_path=discovery_path,
                        parent_reply_id=top_id,
                    )
                )
    return rows


def _merge_paths(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for row in rows:
        key = (str(row.get("item_type") or ""), str(row.get("item_id") or ""))
        if not key[1]:
            continue
        if key not in merged:
            merged[key] = dict(row)
            order.append(key)
            continue
        current = merged[key]
        current["discovery_paths"] = list(
            dict.fromkeys(list(current.get("discovery_paths", [])) + list(row.get("discovery_paths", [])))
        )
        if not current.get("content") and row.get("content"):
            current["content"] = row["content"]
    return [merged[key] for key in order]


def collect_object_followup(
    *,
    run_date: str,
    bars: list[dict[str, str]],
    client: GubaPublicClient,
    max_reply_threads_per_bar: int = 8,
    reply_page_size: int = 50,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    bar_meta: list[dict[str, Any]] = []
    for bar in bars:
        code = str(bar["code"])
        name = str(bar.get("name") or code)
        try:
            payload = client.bar_page(code, page=1)
        except Exception as exc:
            errors.append(f"bar {code}: {type(exc).__name__}: {exc}")
            bar_meta.append({"code": code, "name": name, "error": str(exc)})
            continue
        rows = payload.get("re", []) if isinstance(payload.get("re"), list) else []
        active = [
            row
            for row in rows
            if isinstance(row, dict)
            and (str(row.get("post_publish_time") or "")[:10] == run_date or str(row.get("post_last_time") or "")[:10] == run_date)
        ]
        new_posts = 0
        reply_threads = 0
        reply_hits = 0
        for row in active:
            post_id = str(row.get("post_id") or "").strip()
            if not post_id:
                continue
            post_title = str(row.get("post_title") or "").strip()
            if str(row.get("post_publish_time") or "")[:10] == run_date:
                item = normalize_post_from_list(row, discovery_path="GUBA_OBJECT_FOLLOWUP")
                item["bar_name"] = item.get("bar_name") or name
                items.append(item)
                new_posts += 1
            if str(row.get("post_last_time") or "")[:10] != run_date or _safe_int(row.get("post_comment_count")) <= 0:
                continue
            if reply_threads >= max_reply_threads_per_bar:
                continue
            reply_threads += 1
            try:
                reply_payload = client.post_replies(post_id, page=1, page_size=reply_page_size)
                for item in flatten_replies(
                    reply_payload,
                    code=code,
                    post_title=post_title,
                    discovery_path="GUBA_OBJECT_FOLLOWUP",
                ):
                    if _date_of(item.get("published_at")) == run_date:
                        item["bar_name"] = name
                        items.append(item)
                        reply_hits += 1
            except Exception as exc:
                errors.append(f"replies {code}/{post_id}: {type(exc).__name__}: {exc}")
        bar_meta.append(
            {
                "code": code,
                "name": name,
                "page_rows": len(rows),
                "active_rows": len(active),
                "new_posts": new_posts,
                "reply_threads": reply_threads,
                "reply_hits": reply_hits,
            }
        )
    items = _merge_paths(items)
    return {
        "items": items,
        "bars": bar_meta,
        "error_count": len(errors),
        "errors": errors,
    }


def collect_global_exploration(
    *,
    run_date: str,
    client: GubaPublicClient,
    max_posts: int = 12,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        page = client.index_html()
    except Exception as exc:
        return {"items": [], "error_count": 1, "errors": [f"index: {type(exc).__name__}: {exc}"]}
    soup = BeautifulSoup(page, "html.parser")
    seen: set[str] = set()
    year = int(run_date[:4])
    for li in soup.select("ul.newlist li"):
        note = li.select_one('a.note[href^="/news,"]')
        if note is None:
            continue
        href = str(note.get("href") or "")
        match = re.search(r"/news,([^,]+),(\d+)\.html", href)
        if not match:
            continue
        code, post_id = match.group(1), match.group(2)
        if post_id in seen:
            continue
        published_text = li.select_one("cite.date")
        published = _parse_mmdd_time(published_text.get_text(strip=True) if published_text else "", year=year)
        if not published or published[:10] != run_date:
            continue
        seen.add(post_id)
        try:
            detail = client.post_detail(code, post_id)
            item = normalize_post(detail, discovery_path="GUBA_GLOBAL_EXPLORATION")
            if _date_of(item.get("published_at")) != run_date:
                continue
            items.append(item)
        except Exception as exc:
            errors.append(f"global detail {code}/{post_id}: {type(exc).__name__}: {exc}")
        if len(items) >= max_posts:
            break
    return {"items": _merge_paths(items), "error_count": len(errors), "errors": errors}


def collect_guba_shadow(
    *,
    run_date: str,
    bars: list[dict[str, str]],
    client: GubaPublicClient | None = None,
    include_global: bool = False,
    global_max_posts: int = 12,
    max_reply_threads_per_bar: int = 8,
) -> dict[str, Any]:
    client = client or GubaPublicClient()
    object_result = collect_object_followup(
        run_date=run_date,
        bars=bars,
        client=client,
        max_reply_threads_per_bar=max_reply_threads_per_bar,
    )
    global_result = (
        collect_global_exploration(run_date=run_date, client=client, max_posts=global_max_posts)
        if include_global
        else {"items": [], "error_count": 0, "errors": []}
    )
    items = _merge_paths(list(object_result.get("items", [])) + list(global_result.get("items", [])))
    type_counts: dict[str, int] = {}
    path_counts: dict[str, int] = {}
    for item in items:
        typ = str(item.get("item_type") or "UNKNOWN")
        type_counts[typ] = type_counts.get(typ, 0) + 1
        for path in item.get("discovery_paths", []):
            path_counts[path] = path_counts.get(path, 0) + 1
    return {
        "schema_version": 1,
        "source": "guba",
        "run_date": run_date,
        "mode": "SHADOW_COLLECT_ONLY",
        "bar_count": len(bars),
        "item_count": len(items),
        "item_type_counts": type_counts,
        "discovery_path_counts": path_counts,
        "object_followup": {k: v for k, v in object_result.items() if k != "items"},
        "global_exploration": {k: v for k, v in global_result.items() if k != "items"},
        "request_stats": {
            "requests": client.stats.requests,
            "index_requests": client.stats.index_requests,
            "bar_requests": client.stats.bar_requests,
            "detail_requests": client.stats.detail_requests,
            "reply_requests": client.stats.reply_requests,
        },
        "error_count": int(object_result.get("error_count") or 0) + int(global_result.get("error_count") or 0),
        "errors": list(object_result.get("errors", [])) + list(global_result.get("errors", [])),
        "items": items,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Eastmoney Guba collect-only Shadow probe; no AI and no Radar DB writes.")
    parser.add_argument("--date", required=True)
    parser.add_argument("--watchlist", default=str(DEFAULT_WATCHLIST_PATH))
    parser.add_argument("--include-global", action="store_true", help="Experimental HTML global lane; off by default")
    parser.add_argument("--global-max-posts", type=int, default=12)
    parser.add_argument("--max-reply-threads-per-bar", type=int, default=8)
    parser.add_argument("--show-items", action="store_true")
    args = parser.parse_args()
    result = collect_guba_shadow(
        run_date=args.date,
        bars=load_watchlist(Path(args.watchlist)),
        include_global=args.include_global,
        global_max_posts=args.global_max_posts,
        max_reply_threads_per_bar=args.max_reply_threads_per_bar,
    )
    if not args.show_items:
        result = {k: v for k, v in result.items() if k != "items"}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
