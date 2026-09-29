from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import http.cookiejar
import json
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

from .index_quote import IndexQuote


XUEQIU_HOME = "https://xueqiu.com/"
XUEQIU_QUOTE_URL = "https://stock.xueqiu.com/v5/stock/realtime/quotec.json"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _decimal_or_none(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _timestamp_or_none(value) -> datetime | None:
    if value is None:
        return None
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(
        milliseconds / 1000,
        tz=SHANGHAI_TZ,
    )


def parse_xueqiu_index_quote(
    payload: dict,
    *,
    symbol: str,
) -> IndexQuote:
    rows = payload.get("data") or []
    if not rows:
        return IndexQuote(
            symbol=symbol,
            code=None,
            name=None,
            current=None,
            previous_close=None,
            quote_time=None,
            source="XUEQIU_QUOTE",
            error="NO_INDEX_QUOTE",
        )

    row = rows[0]
    current = _decimal_or_none(row.get("current"))
    previous_close = _decimal_or_none(row.get("last_close"))
    quote_time = _timestamp_or_none(row.get("timestamp"))

    error = None
    if (
        current is None
        or current <= 0
        or previous_close is None
        or previous_close <= 0
        or quote_time is None
    ):
        error = "INVALID_OR_MISSING_INDEX_QUOTE"

    raw_symbol = str(row.get("symbol") or symbol)
    code = raw_symbol
    for prefix in ("CSI", "SH", "SZ"):
        if code.startswith(prefix):
            code = code[len(prefix):]
            break

    return IndexQuote(
        symbol=symbol,
        code=code or None,
        name=None,
        current=current,
        previous_close=previous_close,
        quote_time=quote_time,
        source="XUEQIU_QUOTE",
        error=error,
    )


def fetch_xueqiu_index_quote(
    symbol: str,
    *,
    timeout: int = 10,
) -> IndexQuote:
    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": XUEQIU_HOME,
        "Accept": "application/json,text/plain,*/*",
    }

    home_request = Request(XUEQIU_HOME, headers=headers)
    with opener.open(home_request, timeout=timeout):
        pass

    url = f"{XUEQIU_QUOTE_URL}?{urlencode({'symbol': symbol})}"
    request = Request(url, headers=headers)
    with opener.open(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    return parse_xueqiu_index_quote(payload, symbol=symbol)


def fetch_index_quote_with_fallback(
    *,
    tencent_symbol: str | None,
    xueqiu_symbol: str | None,
    timeout: int = 10,
) -> IndexQuote:
    from .index_quote import fetch_tencent_index_quote

    if tencent_symbol:
        try:
            row = fetch_tencent_index_quote(
                tencent_symbol,
                timeout=timeout,
            )
            if row.error is None:
                return row
        except Exception:
            pass

    if xueqiu_symbol:
        try:
            row = fetch_xueqiu_index_quote(
                xueqiu_symbol,
                timeout=timeout,
            )
            if row.error is None:
                return row
            return row
        except Exception as exc:
            return IndexQuote(
                symbol=xueqiu_symbol,
                code=None,
                name=None,
                current=None,
                previous_close=None,
                quote_time=None,
                source="XUEQIU_QUOTE",
                error=f"FETCH_ERROR:{type(exc).__name__}",
            )

    return IndexQuote(
        symbol=tencent_symbol or xueqiu_symbol or "",
        code=None,
        name=None,
        current=None,
        previous_close=None,
        quote_time=None,
        source="INDEX_QUOTE_FALLBACK",
        error="NO_SUPPORTED_INDEX_QUOTE_SOURCE",
    )
