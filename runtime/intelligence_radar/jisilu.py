from __future__ import annotations

import html as html_lib
import json
import re
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://www.jisilu.cn"
AUTHOR_WATCHLIST_PATH = Path(__file__).with_name("author_watchlist.json")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "Chrome/154.0 Safari/537.36"
)


@dataclass(frozen=True)
class FeedEntry:
    question_id: str
    title: str
    url: str
    category: str | None
    actor: str | None
    activity_kind: str
    activity_at: str
    reply_count: int
    view_count: int
    feed_mode: str


@dataclass(frozen=True)
class AuthorActivityRef:
    question_id: str
    title: str
    url: str
    author_name: str
    author_uid: str
    activity_kind: str
    activity_at: str
    answer_id: str | None = None
    excerpt: str | None = None


def load_author_watchlist(path: Path | None = None) -> list[dict[str, str]]:
    selected = path or AUTHOR_WATCHLIST_PATH
    if not selected.exists():
        return []
    payload = json.loads(selected.read_text(encoding="utf-8"))
    authors = payload.get("authors", []) if isinstance(payload, dict) else []
    rows: list[dict[str, str]] = []
    for row in authors:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        name = str(row.get("user_name") or "").strip()
        uid = str(row.get("uid") or "").strip()
        if name and uid:
            rows.append({"user_name": name, "uid": uid})
    return rows


def _clean_fragment(fragment: str, limit: int | None = None) -> str:
    value = re.sub(r"(?i)<br\s*/?>", "\n", fragment)
    value = re.sub(r"(?i)</(?:p|blockquote|li|div)>", "\n", value)
    value = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", value)
    value = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n\s*", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value).strip()
    if limit is not None and len(value) > limit:
        return value[:limit].rstrip() + "…"
    return value


def _feed_blocks(document: str) -> list[str]:
    return re.findall(r'<div class="aw-item">(.*?)</div>\s*</div>', document, re.S)


def parse_feed(document: str, *, feed_mode: str) -> list[FeedEntry]:
    rows: list[FeedEntry] = []
    for block in _feed_blocks(document):
        q = re.search(
            r'<a[^>]+href="(https?://www\.jisilu\.cn/question/(\d+))"[^>]*>(.*?)</a>',
            block, re.S,
        )
        if q is None:
            continue
        url, question_id, title_html = q.groups()
        text = _clean_fragment(block)
        meta = re.search(
            r"(发起|回复)\s*•\s*(20\d{2}-\d{2}-\d{2} \d{2}:\d{2})"
            r"\s*•\s*([\d,]+)\s*次浏览",
            text,
        )
        if meta is None:
            continue
        activity_kind, activity_at, view_count = meta.groups()
        reply = re.search(
            r'aw-question-replay-count[^"]*"[^>]*>\s*<em>(\d+)</em>',
            block, re.S,
        )
        category_match = re.search(
            r'aw-question-tags.*?<a[^>]*>(.*?)</a>', block, re.S
        )
        people = [
            _clean_fragment(x) for x in re.findall(
                r'<a[^>]+class="aw-user-name"[^>]*>(.*?)</a>', block, re.S
            )
        ]
        people = [x for x in people if x]
        rows.append(
            FeedEntry(
                question_id=question_id,
                title=_clean_fragment(title_html, 240),
                url=url,
                category=_clean_fragment(category_match.group(1), 80) if category_match else None,
                actor=people[-1] if people else None,
                activity_kind=activity_kind,
                activity_at=activity_at,
                reply_count=int(reply.group(1)) if reply else 0,
                view_count=int(view_count.replace(",", "")),
                feed_mode=feed_mode,
            )
        )
    return rows


def _feed_url(feed_mode: str, page: int) -> str:
    if feed_mode == "activity":
        return (
            f"{BASE_URL}/home/explore/"
            if page == 1
            else f"{BASE_URL}/home/explore/sort_type-new__category-__day-0__page-{page}"
        )
    if feed_mode == "new":
        return (
            f"{BASE_URL}/home/explore/category-__sort_type-add_time"
            if page == 1
            else f"{BASE_URL}/home/explore/sort_type-add_time__category-__day-0__page-{page}"
        )
    raise ValueError(f"unsupported feed_mode: {feed_mode}")


def _get(session: requests.Session, url: str, timeout_seconds: int) -> str:
    response = session.get(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9"},
        timeout=timeout_seconds,
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding or "utf-8"
    return response.text


def parse_author_answers(
    document: str,
    *,
    author_name: str,
    author_uid: str,
) -> list[AuthorActivityRef]:
    pattern = re.compile(
        r'href="https?://www\.jisilu\.cn/question/(\d+)\?[^\"]*?answer_id-(\d+)[^\"]*"[^>]*>(.*?)</a>'
        r'.*?<p class="aw-hide-txt">(.*?)</p>'
        r'.*?<p class="aw-text-color-999">\s*(20\d{2}-\d{2}-\d{2} \d{2}:\d{2})\s*</p>',
        re.S,
    )
    rows: list[AuthorActivityRef] = []
    for question_id, answer_id, title_html, excerpt_html, activity_at in pattern.findall(document):
        rows.append(
            AuthorActivityRef(
                question_id=question_id,
                title=_clean_fragment(title_html, 240),
                url=f"{BASE_URL}/question/{question_id}",
                author_name=author_name,
                author_uid=author_uid,
                activity_kind="AUTHOR_ANSWER",
                activity_at=activity_at,
                answer_id=answer_id,
                excerpt=_clean_fragment(excerpt_html, 1200),
            )
        )
    return rows


def parse_author_questions(
    document: str,
    *,
    author_name: str,
    author_uid: str,
) -> list[AuthorActivityRef]:
    pattern = re.compile(
        r'href="https?://www\.jisilu\.cn/question/(\d+)"[^>]*>(.*?)</a>'
        r'.*?<p class="aw-text-color-999">(.*?)</p>',
        re.S,
    )
    rows: list[AuthorActivityRef] = []
    for question_id, title_html, meta_html in pattern.findall(document):
        meta = _clean_fragment(meta_html, 500)
        time_match = re.search(r"(20\d{2}-\d{2}-\d{2} \d{2}:\d{2})", meta)
        if time_match is None:
            continue
        rows.append(
            AuthorActivityRef(
                question_id=question_id,
                title=_clean_fragment(title_html, 240),
                url=f"{BASE_URL}/question/{question_id}",
                author_name=author_name,
                author_uid=author_uid,
                activity_kind="AUTHOR_QUESTION",
                activity_at=time_match.group(1),
            )
        )
    return rows


def _author_activity_url(uid: str, action: str, page: int) -> str:
    return f"{BASE_URL}/people/ajax/user_actions/uid-{uid}__actions-{action}__page-{page}"


def collect_author_activity_refs(
    target_date: str,
    *,
    authors: list[dict[str, str]],
    max_pages: int = 3,
    timeout_seconds: int = 20,
    session: requests.Session | None = None,
) -> tuple[list[AuthorActivityRef], dict[str, Any]]:
    client = session or requests.Session()
    selected: dict[tuple[str, str, str], AuthorActivityRef] = {}
    author_meta: dict[str, Any] = {}
    errors: list[dict[str, str]] = []

    for author in authors:
        name = str(author.get("user_name") or "").strip()
        uid = str(author.get("uid") or "").strip()
        if not name or not uid:
            continue
        meta = {
            "uid": uid,
            "answer_pages": 0,
            "question_pages": 0,
            "answer_hits": 0,
            "question_hits": 0,
        }
        for action, kind in (("201", "answers"), ("101", "questions")):
            for page in range(max(1, max_pages)):
                try:
                    document = _get(
                        client,
                        _author_activity_url(uid, action, page),
                        timeout_seconds,
                    )
                except Exception as exc:
                    errors.append(
                        {
                            "author": name,
                            "kind": kind,
                            "page": str(page),
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                    break
                if not document.strip():
                    break
                if kind == "answers":
                    rows = parse_author_answers(
                        document,
                        author_name=name,
                        author_uid=uid,
                    )
                    meta["answer_pages"] += 1
                else:
                    rows = parse_author_questions(
                        document,
                        author_name=name,
                        author_uid=uid,
                    )
                    meta["question_pages"] += 1
                if not rows:
                    break
                for row in rows:
                    if row.activity_at[:10] != target_date:
                        continue
                    key = (
                        row.question_id,
                        row.activity_kind,
                        row.answer_id or "question",
                    )
                    selected[key] = row
                    if kind == "answers":
                        meta["answer_hits"] += 1
                    else:
                        meta["question_hits"] += 1
                page_dates = [row.activity_at[:10] for row in rows]
                if page_dates and min(page_dates) < target_date:
                    break
                if page_dates and max(page_dates) < target_date:
                    break
        author_meta[name] = meta

    rows = sorted(
        selected.values(),
        key=lambda row: (row.activity_at, row.question_id, row.answer_id or ""),
        reverse=True,
    )
    return rows, {
        "enabled": bool(authors),
        "author_count": len(authors),
        "hit_count": len(rows),
        "authors": author_meta,
        "error_count": len(errors),
        "errors": errors,
    }


def collect_feed_refs(
    target_date: str,
    *,
    max_pages: int = 8,
    timeout_seconds: int = 20,
    session: requests.Session | None = None,
) -> tuple[list[FeedEntry], dict[str, Any]]:
    client = session or requests.Session()
    selected: dict[str, FeedEntry] = {}
    page_counts: dict[str, int] = {}
    for feed_mode in ("activity", "new"):
        scanned_pages = 0
        for page in range(1, max(1, max_pages) + 1):
            entries = parse_feed(
                _get(client, _feed_url(feed_mode, page), timeout_seconds),
                feed_mode=feed_mode,
            )
            scanned_pages += 1
            if not entries:
                break
            page_dates = [row.activity_at[:10] for row in entries]
            for row in entries:
                if row.activity_at[:10] != target_date:
                    continue
                previous = selected.get(row.question_id)
                if previous is None or row.activity_at > previous.activity_at:
                    selected[row.question_id] = row
            if target_date not in page_dates and min(page_dates) < target_date:
                break
            if max(page_dates) < target_date:
                break
        page_counts[feed_mode] = scanned_pages
    rows = sorted(selected.values(), key=lambda row: (row.activity_at, row.question_id), reverse=True)
    return rows, {"feed_pages": page_counts, "question_ref_count": len(rows)}


def _question_author(document: str) -> str | None:
    match = re.search(
        r'<h3>\s*发起人\s*</h3>.*?<a class="aw-user-name"[^>]*>(.*?)</a>',
        document, re.S,
    )
    return _clean_fragment(match.group(1), 120) if match else None


def _question_title(document: str) -> str:
    match = re.search(r'aw-question-detail-title.*?<h1>(.*?)</h1>', document, re.S)
    return _clean_fragment(match.group(1), 240) if match else ""


def _question_text(document: str) -> str:
    match = re.search(
        r'<div class="aw-question-detail-txt markitup-box">(.*?)</div>',
        document, re.S,
    )
    return _clean_fragment(match.group(1), 6000) if match else ""


def _question_published_at(document: str) -> str | None:
    match = re.search(r"发表时间\s*(20\d{2}-\d{2}-\d{2} \d{2}:\d{2})", document)
    return match.group(1) if match else None


def _answer_blocks(document: str) -> list[tuple[str, str]]:
    starts = list(
        re.finditer(
            r'<div class="aw-item"[^>]*id="answer_list_(\d+)"[^>]*>',
            document,
        )
    )
    result: list[tuple[str, str]] = []
    for idx, match in enumerate(starts):
        end = starts[idx + 1].start() if idx + 1 < len(starts) else len(document)
        result.append((match.group(1), document[match.start():end]))
    return result


def parse_question_daily(
    document: str,
    *,
    target_date: str,
    fallback_title: str,
    url: str,
    question_id: str,
    activity_at: str,
    category: str | None,
) -> dict[str, Any]:
    title = _question_title(document) or fallback_title
    author = _question_author(document)
    question_text = _question_text(document)
    published_at = _question_published_at(document)
    declared_match = re.search(r"<h2>\s*(\d+) 个回复\s*</h2>", document)
    declared_replies = int(declared_match.group(1)) if declared_match else 0

    daily_segments: list[dict[str, Any]] = []
    context_segments: list[dict[str, Any]] = []
    if question_text:
        qseg = {
            "segment_id": "question",
            "kind": "QUESTION",
            "author": author,
            "published_at": published_at,
            "text": question_text,
            "is_daily": bool(published_at and published_at[:10] == target_date),
            "locator_id": question_id,
            "locator_url": url,
        }
        context_segments.append(qseg)
        if qseg["is_daily"]:
            daily_segments.append(qseg)

    parsed_times: list[str] = []
    for answer_id, block in _answer_blocks(document):
        time_match = re.search(
            r"(20\d{2}-\d{2}-\d{2} \d{2}:\d{2})(?:修改)?\s+来自",
            block,
        )
        if time_match is None:
            continue
        answer_at = time_match.group(1)
        parsed_times.append(answer_at)
        author_match = re.search(
            r'<p class="publisher"><a class="aw-user-name"[^>]*>(.*?)</a>',
            block, re.S,
        )
        content_match = re.search(
            r'<div class="markitup-box"\s*>(.*?)</div>', block, re.S
        )
        if content_match is None:
            continue
        answer = {
            "segment_id": f"answer_{answer_id}",
            "kind": "ANSWER",
            "author": _clean_fragment(author_match.group(1), 120) if author_match else None,
            "published_at": answer_at,
            "text": _clean_fragment(content_match.group(1), 4000),
            "is_daily": answer_at[:10] == target_date,
            "locator_id": answer_id,
            "locator_url": f"{url}#answer_list_{answer_id}",
        }
        if answer["is_daily"]:
            daily_segments.append(answer)

    coverage_warning = None
    if (
        declared_replies > len(parsed_times)
        and parsed_times
        and min(x[:10] for x in parsed_times) >= target_date
    ):
        coverage_warning = (
            "公开详情页只暴露最近一部分回复，且最早可见回复仍在目标日；"
            "当日回复可能未完全覆盖。"
        )

    return {
        "question_id": question_id,
        "title": title,
        "url": url,
        "category": category,
        "question_author": author,
        "question_published_at": published_at,
        "activity_at": activity_at,
        "context_segments": context_segments,
        "daily_segments": daily_segments,
        "declared_reply_count": declared_replies,
        "visible_reply_count": len(parsed_times),
        "coverage_warning": coverage_warning,
    }


def _author_ref_segment(ref: AuthorActivityRef) -> dict[str, Any] | None:
    if ref.activity_kind != "AUTHOR_ANSWER" or not ref.answer_id:
        return None
    text = str(ref.excerpt or "").strip()
    if not text or text in {"【......】", "......", "…", "【…】"}:
        return None
    return {
        "segment_id": f"answer_{ref.answer_id}",
        "kind": "ANSWER",
        "author": ref.author_name,
        "published_at": ref.activity_at,
        "text": text,
        "is_daily": True,
        "locator_id": ref.answer_id,
        "locator_url": f"{ref.url}#answer_list_{ref.answer_id}",
    }


def collect_daily_candidates(
    target_date: str,
    *,
    max_pages: int = 8,
    max_questions: int = 120,
    timeout_seconds: int = 20,
    request_pause_seconds: float = 0.03,
    author_watchlist: list[dict[str, str]] | None = None,
    author_max_pages: int = 3,
) -> dict[str, Any]:
    date.fromisoformat(target_date)
    session = requests.Session()
    refs, feed_meta = collect_feed_refs(
        target_date,
        max_pages=max_pages,
        timeout_seconds=timeout_seconds,
        session=session,
    )
    authors = load_author_watchlist() if author_watchlist is None else author_watchlist
    try:
        author_refs, author_meta = collect_author_activity_refs(
            target_date,
            authors=authors,
            max_pages=author_max_pages,
            timeout_seconds=timeout_seconds,
            session=session,
        )
    except Exception as exc:
        author_refs = []
        author_meta = {
            "enabled": bool(authors),
            "author_count": len(authors),
            "hit_count": 0,
            "authors": {},
            "error_count": 1,
            "errors": [{"error": f"{type(exc).__name__}: {exc}"}],
        }

    global_qids = {row.question_id for row in refs}
    author_by_qid: dict[str, list[AuthorActivityRef]] = {}
    for row in author_refs:
        author_by_qid.setdefault(row.question_id, []).append(row)

    selected_refs: dict[str, FeedEntry] = {
        row.question_id: row for row in refs[: max(1, max_questions)]
    }
    for qid, hits in author_by_qid.items():
        if qid in selected_refs:
            continue
        latest = max(hits, key=lambda row: row.activity_at)
        selected_refs[qid] = FeedEntry(
            question_id=qid,
            title=latest.title,
            url=latest.url,
            category=None,
            actor=latest.author_name,
            activity_kind=latest.activity_kind,
            activity_at=latest.activity_at,
            reply_count=0,
            view_count=0,
            feed_mode="author",
        )

    author_meta["overlap_question_count"] = len(
        set(author_by_qid).intersection(global_qids)
    )
    author_meta["author_only_question_count"] = len(
        set(author_by_qid).difference(global_qids)
    )
    author_meta["question_ref_count"] = len(author_by_qid)

    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    no_daily_activity = 0
    coverage_warnings = 0

    for ref in sorted(
        selected_refs.values(),
        key=lambda row: (row.activity_at, row.question_id),
        reverse=True,
    ):
        try:
            packet = parse_question_daily(
                _get(session, ref.url, timeout_seconds),
                target_date=target_date,
                fallback_title=ref.title,
                url=ref.url,
                question_id=ref.question_id,
                activity_at=ref.activity_at,
                category=ref.category,
            )
            hits = author_by_qid.get(ref.question_id, [])
            existing_segments = {
                str(seg.get("segment_id")) for seg in packet["daily_segments"]
            }
            for hit in hits:
                segment = _author_ref_segment(hit)
                if segment and segment["segment_id"] not in existing_segments:
                    packet["daily_segments"].append(segment)
                    existing_segments.add(segment["segment_id"])

            discovery_paths = []
            if ref.question_id in global_qids:
                discovery_paths.append("GLOBAL_FEED")
            if hits:
                discovery_paths.append("AUTHOR_LANE")
            packet.update(
                {
                    "feed_actor": ref.actor,
                    "feed_activity_kind": ref.activity_kind,
                    "feed_reply_count": ref.reply_count,
                    "feed_view_count": ref.view_count,
                    "feed_mode": ref.feed_mode,
                    "discovery_paths": discovery_paths,
                    "author_lane_authors": sorted({hit.author_name for hit in hits}),
                    "author_lane_hits": [
                        {
                            "author_name": hit.author_name,
                            "author_uid": hit.author_uid,
                            "activity_kind": hit.activity_kind,
                            "activity_at": hit.activity_at,
                            "answer_id": hit.answer_id,
                        }
                        for hit in hits
                    ],
                }
            )
            if packet["coverage_warning"]:
                coverage_warnings += 1
            if not packet["daily_segments"]:
                no_daily_activity += 1
                continue
            candidates.append(packet)
        except Exception as exc:
            errors.append(
                {
                    "question_id": ref.question_id,
                    "url": ref.url,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
        if request_pause_seconds > 0:
            time.sleep(request_pause_seconds)

    author_candidate_qids = {
        str(row["question_id"])
        for row in candidates
        if "AUTHOR_LANE" in row.get("discovery_paths", [])
    }
    author_only_candidate_qids = {
        str(row["question_id"])
        for row in candidates
        if row.get("discovery_paths") == ["AUTHOR_LANE"]
    }
    author_meta["candidate_count"] = len(author_candidate_qids)
    author_meta["author_only_candidate_count"] = len(author_only_candidate_qids)

    return {
        "run_date": target_date,
        "source": "jisilu",
        "candidate_count": len(candidates),
        "question_ref_count": len(refs),
        "union_question_ref_count": len(selected_refs),
        "candidates": candidates,
        "feed_meta": feed_meta,
        "author_lane_meta": author_meta,
        "detail_error_count": len(errors),
        "detail_errors": errors,
        "detail_without_daily_activity": no_daily_activity,
        "coverage_warning_count": coverage_warnings,
        "truncated_question_count": max(0, len(refs) - min(len(refs), max(1, max_questions))),
    }

