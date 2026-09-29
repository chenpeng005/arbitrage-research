from __future__ import annotations

from dataclasses import dataclass
import html
import json
import re
from pathlib import Path
from typing import Any

from .http_json import fetch_json_with_retry


SSE_UNIVERSE_URL = "https://query.sse.com.cn/commonSoaQuery.do"
SZSE_UNIVERSE_URL = "https://www.szse.cn/api/report/ShowReport/data"

_TAG_RE = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class LofIdentity:
    code: str
    name: str
    exchange: str
    manager: str | None = None
    listing_date: str | None = None
    source: str | None = None


def _clean_html(value: Any) -> str:
    text = "" if value is None else str(value)
    return html.unescape(_TAG_RE.sub("", text)).strip()


def _get_json(
    url: str,
    params: dict[str, Any],
    *,
    referer: str,
    timeout: int = 15,
) -> Any:
    return fetch_json_with_retry(
        url,
        params,
        referer=referer,
        timeout=timeout,
        attempts=3,
        base_delay_seconds=0.25,
    )


def parse_sse_universe(payload: dict[str, Any]) -> list[LofIdentity]:
    rows = payload.get("result") or []
    result: list[LofIdentity] = []
    for row in rows:
        code = str(row.get("fundCode") or "").strip()
        name = str(row.get("secNameFull") or row.get("fundAbbr") or "").strip()
        if not code or not name:
            continue
        result.append(
            LofIdentity(
                code=code,
                name=name,
                exchange="SSE",
                manager=(str(row.get("companyName")).strip() if row.get("companyName") else None),
                listing_date=(
                    str(row.get("listingDate")).strip()
                    if row.get("listingDate")
                    else None
                ),
                source="SSE_OFFICIAL",
            )
        )
    return result


def parse_szse_universe_page(payload: list[dict[str, Any]]) -> list[LofIdentity]:
    if not payload:
        return []
    rows = payload[0].get("data") or []
    result: list[LofIdentity] = []
    for row in rows:
        code = _clean_html(row.get("sys_key"))
        name = _clean_html(row.get("kzjcurl"))
        manager = _clean_html(row.get("glrmc")) or None
        if not code or not name:
            continue
        result.append(
            LofIdentity(
                code=code,
                name=name,
                exchange="SZSE",
                manager=manager,
                source="SZSE_OFFICIAL",
            )
        )
    return result


def fetch_sse_universe(*, timeout: int = 15) -> list[LofIdentity]:
    params = {
        "isPagination": "true",
        "pageHelp.pageSize": 500,
        "pageHelp.pageNo": 1,
        "pageHelp.beginPage": 1,
        "pageHelp.cacheSize": 1,
        "pageHelp.endPage": 1,
        "sqlId": "FUND_LIST",
        "fundType": "10",
        "subClass": "11,14,15",
    }
    payload = _get_json(
        SSE_UNIVERSE_URL,
        params,
        referer="https://www.sse.com.cn/assortment/fund/lof/home/",
        timeout=timeout,
    )
    return parse_sse_universe(payload)


def fetch_szse_universe(*, timeout: int = 15) -> list[LofIdentity]:
    base_params = {
        "SHOWTYPE": "JSON",
        "CATALOGID": "fund_lof",
    }
    first_payload = _get_json(
        SZSE_UNIVERSE_URL,
        {**base_params, "PAGENO": 1},
        referer="https://fund.szse.cn/marketdata/lof/",
        timeout=timeout,
    )

    result = parse_szse_universe_page(first_payload)
    page_count = 1
    if first_payload:
        metadata = first_payload[0].get("metadata") or {}
        page_count = int(metadata.get("pagecount") or 1)

    for page_no in range(2, page_count + 1):
        payload = _get_json(
            SZSE_UNIVERSE_URL,
            {**base_params, "PAGENO": page_no},
            referer="https://fund.szse.cn/marketdata/lof/",
            timeout=timeout,
        )
        result.extend(parse_szse_universe_page(payload))

    return result


def _load_universe_fixture(
    path: str | Path,
    *,
    exchange: str,
) -> list[LofIdentity]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("rows") or []
    result: list[LofIdentity] = []
    for row in rows:
        if str(row.get("exchange") or "") != exchange:
            continue
        code = str(row.get("code") or "").strip()
        name = str(row.get("name") or "").strip()
        if not code or not name:
            continue
        result.append(
            LofIdentity(
                code=code,
                name=name,
                exchange=exchange,
                manager=(
                    str(row.get("manager")).strip()
                    if row.get("manager")
                    else None
                ),
                listing_date=(
                    str(row.get("listing_date")).strip()
                    if row.get("listing_date")
                    else None
                ),
                source=str(
                    row.get("source")
                    or f"{exchange}_OFFICIAL_FIXTURE"
                ),
            )
        )
    if not result:
        raise ValueError(f"{exchange} universe fixture is empty")
    return sorted(result, key=lambda item: item.code)


def load_sse_universe_fixture(
    path: str | Path,
) -> list[LofIdentity]:
    return _load_universe_fixture(path, exchange="SSE")


def load_szse_universe_fixture(
    path: str | Path,
) -> list[LofIdentity]:
    return _load_universe_fixture(path, exchange="SZSE")


def fetch_all_lof_universe(
    *,
    timeout: int = 15,
    sse_fixture_path: str | Path | None = None,
    szse_fixture_path: str | Path | None = None,
) -> list[LofIdentity]:
    sse_rows = (
        load_sse_universe_fixture(sse_fixture_path)
        if sse_fixture_path is not None
        else fetch_sse_universe(timeout=timeout)
    )
    szse_rows = (
        load_szse_universe_fixture(szse_fixture_path)
        if szse_fixture_path is not None
        else fetch_szse_universe(timeout=timeout)
    )
    rows = [
        *sse_rows,
        *szse_rows,
    ]

    unique: dict[tuple[str, str], LofIdentity] = {}
    for row in rows:
        unique[(row.exchange, row.code)] = row

    return sorted(unique.values(), key=lambda item: (item.exchange, item.code))
