from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
from typing import Any, Iterable

from .http_json import fetch_json_with_retry
from .universe import LofIdentity


SSE_NAV_URL = "https://query.sse.com.cn/commonQuery.do"
SZSE_NAV_URL = "https://www.szse.cn/api/report/ShowReport/data"


@dataclass(frozen=True)
class OfficialNavRecord:
    code: str
    exchange: str
    nav: Decimal | None
    nav_date: date | None
    fetched_at: datetime
    source: str
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.nav is not None and self.nav_date is not None and self.error is None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        result = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return result if result > 0 else None


def _date_or_none(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def _get_json(
    url: str,
    params: dict[str, Any],
    *,
    referer: str,
    timeout: int,
) -> Any:
    return fetch_json_with_retry(
        url,
        params,
        referer=referer,
        timeout=timeout,
        attempts=3,
        base_delay_seconds=0.25,
    )


def parse_sse_nav_payload(
    payload: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
) -> list[OfficialNavRecord]:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    page_help = payload.get("pageHelp") or {}
    rows = page_help.get("data") or []
    result: list[OfficialNavRecord] = []

    for row in rows:
        code = str(row.get("FUND_CODE") or "").strip()
        if not code:
            continue
        nav = _decimal_or_none(row.get("NAV"))
        nav_date = _date_or_none(row.get("ASSESS_DATE"))
        error = None
        if nav is None or nav_date is None:
            error = "INVALID_OR_MISSING_NAV"
        result.append(
            OfficialNavRecord(
                code=code,
                exchange="SSE",
                nav=nav,
                nav_date=nav_date,
                fetched_at=fetched_at,
                source="SSE_OFFICIAL",
                error=error,
            )
        )
    return result


def parse_szse_nav_payload(
    payload: list[dict[str, Any]],
    *,
    requested_code: str,
    fetched_at: datetime | None = None,
) -> OfficialNavRecord:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    if payload:
        rows = payload[0].get("data") or []

    if not rows:
        return OfficialNavRecord(
            code=requested_code,
            exchange="SZSE",
            nav=None,
            nav_date=None,
            fetched_at=fetched_at,
            source="SZSE_OFFICIAL",
            error="NO_NAV_DATA",
        )

    # The official endpoint returns recent history for the requested fund.
    # Use the first valid row and verify the returned fund code.
    for row in rows:
        code = str(row.get("fund_code") or "").strip()
        if code and code != requested_code:
            continue
        nav = _decimal_or_none(row.get("nav_per_share"))
        nav_date = _date_or_none(row.get("nav_date"))
        if nav is None or nav_date is None:
            continue
        return OfficialNavRecord(
            code=requested_code,
            exchange="SZSE",
            nav=nav,
            nav_date=nav_date,
            fetched_at=fetched_at,
            source="SZSE_OFFICIAL",
            error=None,
        )

    return OfficialNavRecord(
        code=requested_code,
        exchange="SZSE",
        nav=None,
        nav_date=None,
        fetched_at=fetched_at,
        source="SZSE_OFFICIAL",
        error="INVALID_OR_MISMATCHED_NAV_DATA",
    )


def fetch_sse_official_nav(*, timeout: int = 15) -> list[OfficialNavRecord]:
    params = {
        "isPagination": "true",
        "PRODUCT_TYPE": "11,14,15",
        "SEARCH_DATE": "",
        "type": "inParams",
        "sqlId": "COMMON_SSE_CP_JJ_LOF_SSKFSJJJZ_L",
        "pageHelp.pageSize": 500,
        "pageHelp.pageNo": 1,
        "pageHelp.cacheSize": 1,
        "pagecache": "false",
    }
    payload = _get_json(
        SSE_NAV_URL,
        params,
        referer="https://www.sse.com.cn/assortment/fund/lof/netvalue/",
        timeout=timeout,
    )
    return parse_sse_nav_payload(payload)


def fetch_szse_official_nav_for_code(
    code: str,
    *,
    timeout: int = 15,
) -> OfficialNavRecord:
    payload = _get_json(
        SZSE_NAV_URL,
        {
            "SHOWTYPE": "JSON",
            "CATALOGID": "fund_jjjz",
            "TABKEY": "tab1",
            "txtDm": code,
        },
        referer="https://fund.szse.cn/marketdata/lof/",
        timeout=timeout,
    )
    return parse_szse_nav_payload(payload, requested_code=code)


def fetch_szse_official_nav(
    codes: Iterable[str],
    *,
    timeout: int = 15,
    max_workers: int = 8,
) -> list[OfficialNavRecord]:
    code_list = sorted({str(code).strip() for code in codes if str(code).strip()})
    if not code_list:
        return []

    results: dict[str, OfficialNavRecord] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(
                fetch_szse_official_nav_for_code,
                code,
                timeout=timeout,
            ): code
            for code in code_list
        }
        for future in as_completed(future_map):
            code = future_map[future]
            try:
                results[code] = future.result()
            except Exception as exc:
                results[code] = OfficialNavRecord(
                    code=code,
                    exchange="SZSE",
                    nav=None,
                    nav_date=None,
                    fetched_at=datetime.now(timezone.utc),
                    source="SZSE_OFFICIAL",
                    error=f"FETCH_ERROR:{type(exc).__name__}",
                )

    return [results[code] for code in code_list]


def fetch_all_official_nav(
    universe: Iterable[LofIdentity],
    *,
    timeout: int = 15,
    szse_max_workers: int = 8,
) -> list[OfficialNavRecord]:
    rows = list(universe)
    sse_codes = {row.code for row in rows if row.exchange == "SSE"}
    szse_codes = [row.code for row in rows if row.exchange == "SZSE"]

    sse_error: str | None = None
    szse_error: str | None = None

    try:
        sse_rows = [
            row
            for row in fetch_sse_official_nav(timeout=timeout)
            if row.code in sse_codes
        ]
    except Exception as exc:
        sse_rows = []
        sse_error = f"FETCH_ERROR:{type(exc).__name__}"

    try:
        szse_rows = fetch_szse_official_nav(
            szse_codes,
            timeout=timeout,
            max_workers=szse_max_workers,
        )
    except Exception as exc:
        szse_rows = []
        szse_error = f"FETCH_ERROR:{type(exc).__name__}"

    by_key: dict[tuple[str, str], OfficialNavRecord] = {
        (row.exchange, row.code): row for row in [*sse_rows, *szse_rows]
    }

    # Preserve explicit missing records for every universe member.
    result: list[OfficialNavRecord] = []
    for item in rows:
        key = (item.exchange, item.code)
        record = by_key.get(key)
        if record is None:
            record = OfficialNavRecord(
                code=item.code,
                exchange=item.exchange,
                nav=None,
                nav_date=None,
                fetched_at=datetime.now(timezone.utc),
                source=(
                    "SSE_OFFICIAL"
                    if item.exchange == "SSE"
                    else "SZSE_OFFICIAL"
                ),
                error=(
                    sse_error
                    if item.exchange == "SSE" and sse_error
                    else szse_error
                    if item.exchange == "SZSE" and szse_error
                    else "MISSING_FROM_OFFICIAL_NAV_SOURCE"
                ),
            )
        result.append(record)

    return sorted(result, key=lambda item: (item.exchange, item.code))


def load_official_nav_fixture(
    path: str | Path,
) -> list[OfficialNavRecord]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    fetched_at_raw = payload.get("fetched_at")
    fetched_at = (
        datetime.fromisoformat(str(fetched_at_raw))
        if fetched_at_raw
        else datetime.now(timezone.utc)
    )
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)

    result: list[OfficialNavRecord] = []
    for row in payload.get("rows") or []:
        code = str(row.get("code") or "").strip()
        exchange = str(row.get("exchange") or "").strip()
        if not code or exchange not in {"SSE", "SZSE"}:
            continue
        nav = _decimal_or_none(row.get("nav"))
        nav_date = _date_or_none(row.get("nav_date"))
        error = None
        if nav is None or nav_date is None:
            error = "INVALID_OR_MISSING_NAV_FIXTURE"
        result.append(
            OfficialNavRecord(
                code=code,
                exchange=exchange,
                nav=nav,
                nav_date=nav_date,
                fetched_at=fetched_at,
                source=str(row.get("source") or "OFFICIAL_NAV_FIXTURE"),
                error=error,
            )
        )

    if not result:
        raise ValueError("official NAV fixture is empty")
    return sorted(result, key=lambda item: (item.exchange, item.code))


def nav_age_days(record: OfficialNavRecord, *, as_of: date) -> int | None:
    if record.nav_date is None:
        return None
    return (as_of - record.nav_date).days


def is_nav_stale(
    record: OfficialNavRecord,
    *,
    as_of: date,
    max_age_calendar_days: int,
) -> bool:
    """Configurable freshness check.

    Calendar-aware trading rules belong to a later calendar resolver. This helper
    deliberately requires the caller to provide the tolerated age instead of
    silently assuming that T-1 is always current.
    """
    age = nav_age_days(record, as_of=as_of)
    return age is None or age > max_age_calendar_days
