from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .index_quote import IndexQuote


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _parse_time(value: str | None) -> datetime | None:
    text = (value or "").strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y%m%d%H%M%S",
    ):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=SHANGHAI_TZ)
        except ValueError:
            pass
    return None


def parse_tencent_foreign_quote(
    text: str,
    *,
    symbol: str,
) -> IndexQuote:
    marker = f'v_{symbol}="'
    start = text.find(marker)
    if start < 0:
        return IndexQuote(
            symbol=symbol,
            code=None,
            name=None,
            current=None,
            previous_close=None,
            quote_time=None,
            source="TENCENT_FOREIGN_QUOTE",
            error="NO_QUOTE",
        )

    body = text[start + len(marker):].split('"', 1)[0]
    fields = body.split("~")
    if len(fields) < 31:
        return IndexQuote(
            symbol=symbol,
            code=None,
            name=None,
            current=None,
            previous_close=None,
            quote_time=None,
            source="TENCENT_FOREIGN_QUOTE",
            error="INVALID_QUOTE",
        )

    current = _decimal(fields[3])
    previous_close = _decimal(fields[4])
    quote_time = _parse_time(fields[30])
    error = None
    if current is None or current <= 0 or quote_time is None:
        error = "INVALID_OR_MISSING_QUOTE"

    return IndexQuote(
        symbol=symbol,
        code=(fields[2].strip() or None),
        name=(fields[1].strip() or None),
        current=current,
        previous_close=previous_close,
        quote_time=quote_time,
        source="TENCENT_FOREIGN_QUOTE",
        error=error,
    )


def fetch_tencent_foreign_quote(
    symbol: str,
    *,
    timeout: int = 8,
) -> IndexQuote:
    request = Request(
        f"{TENCENT_QUOTE_URL}{symbol}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("gb18030", errors="replace")
    return parse_tencent_foreign_quote(text, symbol=symbol)
