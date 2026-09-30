from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .index_quote import IndexQuote


CSI_INDEX_QUOTE_URL = (
    "https://www.csindex.com.cn/csindex-home/perf/index-perf-oneday"
)
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _quote_time_or_none(
    trade_date,
    trade_time,
) -> datetime | None:
    text = f"{str(trade_date or '').strip()} {str(trade_time or '').strip()}"
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(
            tzinfo=SHANGHAI_TZ
        )
    except ValueError:
        return None


def supports_csindex_official_quote(index_code: str | None) -> bool:
    code = str(index_code or "").strip().upper()
    return bool(
        re.fullmatch(r"9(?:30|31|32|33)\d{3}", code)
        or re.fullmatch(r"H\d{5}", code)
    )


def parse_csindex_intraday_payload(
    payload: dict,
    *,
    index_code: str,
) -> IndexQuote:
    data = payload.get("data") or {}
    header = data.get("intraDayHeader")
    if not isinstance(header, dict):
        return IndexQuote(
            symbol=index_code,
            code=index_code,
            name=None,
            current=None,
            previous_close=None,
            quote_time=None,
            source="CSI_OFFICIAL_INTRADAY",
            error="NO_INDEX_QUOTE",
        )

    current = _decimal_or_none(header.get("current"))
    previous_close = _decimal_or_none(header.get("closePre"))
    quote_time = _quote_time_or_none(
        header.get("tradeDate"),
        header.get("tradeTime"),
    )
    code = str(header.get("indexCode") or index_code).strip().upper()

    error = None
    if (
        current is None
        or current <= 0
        or previous_close is None
        or previous_close <= 0
        or quote_time is None
    ):
        error = "INVALID_OR_MISSING_INDEX_QUOTE"

    return IndexQuote(
        symbol=index_code,
        code=code or index_code,
        name=None,
        current=current,
        previous_close=previous_close,
        quote_time=quote_time,
        source="CSI_OFFICIAL_INTRADAY",
        error=error,
    )


def fetch_csindex_index_quote(
    index_code: str,
    *,
    timeout: int = 10,
) -> IndexQuote:
    code = str(index_code or "").strip().upper()
    url = f"{CSI_INDEX_QUOTE_URL}?{urlencode({'indexCode': code})}"
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.csindex.com.cn/",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_csindex_intraday_payload(
        payload,
        index_code=code,
    )


def fetch_csindex_index_quotes(
    index_codes: Iterable[str],
    *,
    timeout: int = 10,
    max_workers: int = 8,
) -> dict[str, IndexQuote]:
    codes = sorted(
        {
            str(code).strip().upper()
            for code in index_codes
            if supports_csindex_official_quote(str(code))
        }
    )
    if not codes:
        return {}

    result: dict[str, IndexQuote] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(
                fetch_csindex_index_quote,
                code,
                timeout=timeout,
            ): code
            for code in codes
        }
        for future in as_completed(future_map):
            code = future_map[future]
            try:
                result[code] = future.result()
            except Exception as exc:
                result[code] = IndexQuote(
                    symbol=code,
                    code=code,
                    name=None,
                    current=None,
                    previous_close=None,
                    quote_time=None,
                    source="CSI_OFFICIAL_INTRADAY",
                    error=f"FETCH_ERROR:{type(exc).__name__}",
                )

    return result
