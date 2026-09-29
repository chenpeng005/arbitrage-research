from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class IndexQuote:
    symbol: str
    code: str | None
    name: str | None
    current: Decimal | None
    previous_close: Decimal | None
    quote_time: datetime | None
    source: str
    error: str | None = None


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


def _time_or_none(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d%H%M%S").replace(
            tzinfo=SHANGHAI_TZ
        )
    except ValueError:
        return None


def parse_tencent_index_quote(
    text: str,
    *,
    symbol: str,
) -> IndexQuote:
    marker = f"v_{symbol}=\""
    start = text.find(marker)
    if start < 0:
        return IndexQuote(
            symbol=symbol,
            code=None,
            name=None,
            current=None,
            previous_close=None,
            quote_time=None,
            source="TENCENT_QUOTE",
            error="NO_INDEX_QUOTE",
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
            source="TENCENT_QUOTE",
            error="INVALID_INDEX_QUOTE",
        )

    current = _decimal_or_none(fields[3])
    previous_close = _decimal_or_none(fields[4])
    quote_time = _time_or_none(fields[30])
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
        symbol=symbol,
        code=(fields[2].strip() or None),
        name=(fields[1].strip() or None),
        current=current,
        previous_close=previous_close,
        quote_time=quote_time,
        source="TENCENT_QUOTE",
        error=error,
    )


def fetch_tencent_index_quote(
    symbol: str,
    *,
    timeout: int = 10,
) -> IndexQuote:
    request = Request(
        f"{TENCENT_QUOTE_URL}{symbol}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    text = raw.decode("gb18030", errors="replace")
    return parse_tencent_index_quote(text, symbol=symbol)
