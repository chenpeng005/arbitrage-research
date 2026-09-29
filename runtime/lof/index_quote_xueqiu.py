from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import http.cookiejar
import json
from typing import Iterable
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


def parse_xueqiu_index_quote_response(
    payload: dict,
) -> dict[str, IndexQuote]:
    result: dict[str, IndexQuote] = {}
    for row in payload.get("data") or []:
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            continue
        result[symbol] = parse_xueqiu_index_quote(
            {"data": [row]},
            symbol=symbol,
        )
    return result


def fetch_xueqiu_index_quotes(
    symbols: Iterable[str],
    *,
    timeout: int = 10,
    batch_size: int = 50,
) -> dict[str, IndexQuote]:
    symbol_list = sorted({str(x).strip() for x in symbols if str(x).strip()})
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    cookie_jar = http.cookiejar.CookieJar()
    opener = build_opener(HTTPCookieProcessor(cookie_jar))
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": XUEQIU_HOME,
        "Accept": "application/json,text/plain,*/*",
    }

    try:
        with opener.open(Request(XUEQIU_HOME, headers=headers), timeout=timeout):
            pass
    except Exception:
        # Quote request may still work with an existing edge/session cookie path.
        pass

    result: dict[str, IndexQuote] = {}
    for start in range(0, len(symbol_list), batch_size):
        batch = symbol_list[start : start + batch_size]
        url = (
            f"{XUEQIU_QUOTE_URL}?"
            f"{urlencode({'symbol': ','.join(batch)})}"
        )
        try:
            with opener.open(Request(url, headers=headers), timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            result.update(parse_xueqiu_index_quote_response(payload))
        except Exception as exc:
            for symbol in batch:
                result[symbol] = IndexQuote(
                    symbol=symbol,
                    code=None,
                    name=None,
                    current=None,
                    previous_close=None,
                    quote_time=None,
                    source="XUEQIU_QUOTE",
                    error=f"FETCH_ERROR:{type(exc).__name__}",
                )

        for symbol in batch:
            if symbol not in result:
                result[symbol] = IndexQuote(
                    symbol=symbol,
                    code=None,
                    name=None,
                    current=None,
                    previous_close=None,
                    quote_time=None,
                    source="XUEQIU_QUOTE",
                    error="MISSING_FROM_BATCH_QUOTE",
                )

    return result


def fetch_index_quotes_for_mappings(
    mappings: Iterable["IndexProxyMapping"],
    *,
    timeout: int = 10,
) -> dict[tuple[str | None, str | None], IndexQuote]:
    from .index_proxy import IndexProxyMapping
    from .index_quote import fetch_tencent_index_quotes

    mapping_list = list(mappings)
    tencent_symbols = [
        x.tencent_symbol for x in mapping_list if x.tencent_symbol
    ]
    tencent_quotes = fetch_tencent_index_quotes(
        tencent_symbols,
        timeout=timeout,
    )

    need_xueqiu: list[str] = []
    for mapping in mapping_list:
        if not mapping.xueqiu_symbol:
            continue
        tq = (
            tencent_quotes.get(mapping.tencent_symbol)
            if mapping.tencent_symbol
            else None
        )
        if tq is None or tq.error is not None:
            need_xueqiu.append(mapping.xueqiu_symbol)

    xueqiu_quotes = fetch_xueqiu_index_quotes(
        need_xueqiu,
        timeout=timeout,
    )

    result: dict[tuple[str | None, str | None], IndexQuote] = {}
    for mapping in mapping_list:
        key = (mapping.tencent_symbol, mapping.xueqiu_symbol)
        quote = (
            tencent_quotes.get(mapping.tencent_symbol)
            if mapping.tencent_symbol
            else None
        )
        if quote is None or quote.error is not None:
            fallback = (
                xueqiu_quotes.get(mapping.xueqiu_symbol)
                if mapping.xueqiu_symbol
                else None
            )
            if fallback is not None:
                quote = fallback

        if quote is None:
            quote = IndexQuote(
                symbol=mapping.tencent_symbol or mapping.xueqiu_symbol or "",
                code=mapping.index_code,
                name=mapping.index_name,
                current=None,
                previous_close=None,
                quote_time=None,
                source="INDEX_QUOTE_FALLBACK",
                error="NO_SUPPORTED_INDEX_QUOTE_SOURCE",
            )
        result[key] = quote

    return result
