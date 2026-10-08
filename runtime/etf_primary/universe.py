from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import html
import re
from typing import Any
from urllib.parse import unquote

from .classification import classify_etf
from .http import fetch_json
from .models import EtfIdentity
from .szse_relay import (
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_RELAY_BASE_URL,
    fetch_szse_etf_relay_bundle,
    validate_relay_freshness,
)


SSE_LIST_API = "https://query.sse.com.cn/commonSoaQuery.do"
SSE_LIST_PAGE = "https://www.sse.com.cn/assortment/fund/etf/list/"
SSE_PCF_DETAIL = "https://www.sse.com.cn/disclosure/fund/etflist/detail.shtml"

SZSE_LIST_API = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_LIST_PAGE = "https://www.szse.cn/market/product/list/etfList/index.html"
SZSE_PCF_PAGE = "https://www.szse.cn/disclosure/fund/currency/index.html"

SSE_SUBCLASSES = ("01", "02", "03", "04", "05", "06", "08", "09", "31", "32", "33", "37")

_TAG_RE = re.compile(r"<[^>]+>")
_CODE_RE = re.compile(r"code=(\d{6})", flags=re.I)
_NAME_PARAM_RE = re.compile(r"(?:\?|&|\b)name=([^&\"']+)", flags=re.I)


def _plain(value: Any) -> str:
    return html.unescape(_TAG_RE.sub("", "" if value is None else str(value))).strip()


def _extract_sse_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = payload.get("result")
    if isinstance(rows, list):
        return rows
    page_help = payload.get("pageHelp") or {}
    rows = page_help.get("data")
    return rows if isinstance(rows, list) else []


def _merge_raw_class(existing: str | None, new_class: str) -> str:
    values = [item for item in (existing or "").split(",") if item]
    if new_class not in values:
        values.append(new_class)
    return ",".join(values)


def fetch_sse_etf_universe(*, timeout: int = 20) -> list[EtfIdentity]:
    by_code: dict[str, EtfIdentity] = {}
    for subclass in SSE_SUBCLASSES:
        page_no = 1
        while True:
            payload = fetch_json(
                SSE_LIST_API,
                {
                    "isPagination": "true",
                    "pageHelp.pageSize": 500,
                    "pageHelp.pageNo": page_no,
                    "pageHelp.beginPage": page_no,
                    "pageHelp.endPage": page_no,
                    "pageHelp.cacheSize": 1,
                    "sqlId": "FUND_LIST",
                    "fundType": "00",
                    "subClass": subclass,
                },
                referer=SSE_LIST_PAGE,
                timeout=timeout,
            )
            rows = _extract_sse_rows(payload)
            for row in rows:
                code = str(row.get("fundCode") or "").strip()
                name = str(row.get("secNameFull") or row.get("fundAbbr") or "").strip()
                if not code or not name:
                    continue
                tracking = str(row.get("INDEX_NAME") or row.get("INDEX_CODE") or "").strip() or None
                if code in by_code:
                    old = by_code[code]
                    by_code[code] = replace(
                        old,
                        raw_exchange_class=_merge_raw_class(old.raw_exchange_class, subclass),
                    )
                    continue

                cls = classify_etf(
                    name=name,
                    tracking_index=tracking,
                    exchange="SSE",
                    raw_exchange_class=subclass,
                )
                by_code[code] = EtfIdentity(
                    code=code,
                    name=name,
                    exchange="SSE",
                    manager=str(row.get("companyName") or "").strip() or None,
                    tracking_index=tracking,
                    listing_date=str(row.get("listingDate") or "").strip() or None,
                    raw_exchange_class=subclass,
                    universe_source_url=SSE_LIST_PAGE,
                    pcf_page_url=f"{SSE_PCF_DETAIL}?fundid={code}",
                    **cls,
                )

            if len(rows) < 500:
                break
            page_no += 1

    return sorted(by_code.values(), key=lambda item: item.code)


def _extract_szse_code(value: Any) -> str:
    text = "" if value is None else str(value)
    match = _CODE_RE.search(text)
    if match:
        return match.group(1)
    plain = _plain(text)
    match = re.search(r"\b(1[56]\d{4})\b", plain)
    return match.group(1) if match else ""


def _extract_szse_name(value: Any) -> str:
    text = "" if value is None else str(value)
    plain = _plain(text)
    if plain and plain != text:
        return plain
    match = _NAME_PARAM_RE.search(text)
    if match:
        return unquote(match.group(1)).strip()
    return plain


def _parse_szse_page(payload: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    if not payload:
        return [], 0
    block = payload[0] or {}
    rows = block.get("data") or []
    meta = block.get("metadata") or {}
    return rows, int(meta.get("pagecount") or 1)


def _build_szse_identity(
    *,
    code: str,
    name: str,
    manager: str | None,
    tracking_index: str | None,
    listing_date: str | None = None,
) -> EtfIdentity:
    cls = classify_etf(
        name=name,
        tracking_index=tracking_index,
        exchange="SZSE",
    )
    return EtfIdentity(
        code=code,
        name=name,
        exchange="SZSE",
        manager=manager,
        tracking_index=tracking_index,
        listing_date=listing_date,
        raw_exchange_class=None,
        universe_source_url=SZSE_LIST_PAGE,
        pcf_page_url=SZSE_PCF_PAGE,
        **cls,
    )


def fetch_szse_etf_universe(*, timeout: int = 20, throttle_seconds: float = 0.0) -> list[EtfIdentity]:
    import time

    first = fetch_json(
        SZSE_LIST_API,
        {
            "SHOWTYPE": "JSON",
            "CATALOGID": "1945",
            "tab1PAGESIZE": 10,
            "tab1PAGENO": 1,
            "loading": "first",
        },
        referer=SZSE_LIST_PAGE,
        timeout=timeout,
    )
    rows, page_count = _parse_szse_page(first)
    all_rows = list(rows)

    for page_no in range(2, page_count + 1):
        if throttle_seconds > 0:
            time.sleep(throttle_seconds)
        payload = fetch_json(
            SZSE_LIST_API,
            {
                "SHOWTYPE": "JSON",
                "CATALOGID": "1945",
                "tab1PAGESIZE": 10,
                "tab1PAGENO": page_no,
            },
            referer=SZSE_LIST_PAGE,
            timeout=timeout,
        )
        page_rows, _ = _parse_szse_page(payload)
        all_rows.extend(page_rows)

    result: dict[str, EtfIdentity] = {}
    for row in all_rows:
        code = _extract_szse_code(row.get("sys_key"))
        name = _extract_szse_name(row.get("kzjcurl"))
        if not code or not name:
            continue
        result[code] = _build_szse_identity(
            code=code,
            name=name,
            manager=_plain(row.get("glrmc")) or None,
            tracking_index=_plain(row.get("nhzs")) or None,
        )

    if len(result) < 400:
        raise ValueError(f"SZSE official ETF universe sanity floor failed: {len(result)}")
    return sorted(result.values(), key=lambda item: item.code)


def load_szse_etf_universe_from_relay(
    *,
    base_url: str = DEFAULT_RELAY_BASE_URL,
    timeout: int = 20,
    as_of: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
) -> list[EtfIdentity]:
    bundle = fetch_szse_etf_relay_bundle(base_url, timeout=timeout)
    resolved_as_of = as_of or datetime.now(timezone.utc)
    if resolved_as_of.tzinfo is None:
        resolved_as_of = resolved_as_of.replace(tzinfo=timezone.utc)
    validate_relay_freshness(
        bundle,
        as_of=resolved_as_of,
        max_age_seconds=max_age_seconds,
    )

    result: dict[str, EtfIdentity] = {}
    for row in bundle.universe.get("rows") or []:
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            continue
        if code in result:
            raise ValueError(f"duplicate SZSE ETF relay code: {code}")
        result[code] = _build_szse_identity(
            code=code,
            name=name,
            manager=str(row.get("manager") or "").strip() or None,
            tracking_index=str(row.get("tracking_index") or "").strip() or None,
            listing_date=str(row.get("listing_date") or "").strip() or None,
        )

    if len(result) < 400:
        raise ValueError(f"SZSE ETF relay universe sanity floor failed: {len(result)}")
    return sorted(result.values(), key=lambda item: item.code)


def fetch_all_etf_universe(
    *,
    timeout: int = 20,
    szse_throttle_seconds: float = 0.0,
    szse_relay_base_url: str | None = DEFAULT_RELAY_BASE_URL,
    as_of: datetime | None = None,
) -> list[EtfIdentity]:
    sse_rows = fetch_sse_etf_universe(timeout=timeout)
    try:
        szse_rows = fetch_szse_etf_universe(
            timeout=timeout,
            throttle_seconds=szse_throttle_seconds,
        )
    except Exception:
        if not szse_relay_base_url:
            raise
        szse_rows = load_szse_etf_universe_from_relay(
            base_url=szse_relay_base_url,
            timeout=timeout,
            as_of=as_of,
        )

    rows = [*sse_rows, *szse_rows]
    return sorted(rows, key=lambda item: (item.exchange, item.code))
