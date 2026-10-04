from __future__ import annotations

import html as html_lib
import re
import time
from dataclasses import dataclass
from datetime import date
from typing import Any

import requests

BASE_URL = "https://www.jisilu.cn"
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


def collect_daily_candidates(
    target_date: str,
    *,
    max_pages: int = 8,
    max_questions: int = 120,
    timeout_seconds: int = 20,
    request_pause_seconds: float = 0.03,
) -> dict[str, Any]:
    date.fromisoformat(target_date)
    session = requests.Session()
    refs, feed_meta = collect_feed_refs(
        target_date,
        max_pages=max_pages,
        timeout_seconds=timeout_seconds,
        session=session,
    )
    selected_refs = refs[: max(1, max_questions)]
    candidates: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    no_daily_activity = 0
    coverage_warnings = 0

    for ref in selected_refs:
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
            packet.update(
                {
                    "feed_actor": ref.actor,
                    "feed_activity_kind": ref.activity_kind,
                    "feed_reply_count": ref.reply_count,
                    "feed_view_count": ref.view_count,
                    "feed_mode": ref.feed_mode,
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

    return {
        "run_date": target_date,
        "source": "jisilu",
        "candidate_count": len(candidates),
        "question_ref_count": len(refs),
        "candidates": candidates,
        "feed_meta": feed_meta,
        "detail_error_count": len(errors),
        "detail_errors": errors,
        "detail_without_daily_activity": no_daily_activity,
        "coverage_warning_count": coverage_warnings,
        "truncated_question_count": max(0, len(refs) - len(selected_refs)),
    }
