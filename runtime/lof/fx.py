from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class FxQuote:
    symbol: str
    current: Decimal | None
    quote_time: datetime | None
    source: str
    error: str | None = None


@dataclass(frozen=True)
class FxDailyClose:
    date: date
    close: Decimal


def _decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_tencent_fx_quote(text: str, *, symbol: str) -> FxQuote:
    marker = f'v_{symbol}="'
    start = text.find(marker)
    if start < 0:
        return FxQuote(symbol, None, None, "TENCENT_FX", "NO_QUOTE")
    body = text[start + len(marker):].split('"', 1)[0]
    fields = body.split("~")
    if len(fields) < 6:
        return FxQuote(symbol, None, None, "TENCENT_FX", "INVALID_QUOTE")

    current = _decimal(fields[3])
    quote_time = None
    raw_time = fields[5].strip()
    if len(raw_time) == 14 and raw_time.isdigit():
        try:
            quote_time = datetime.strptime(
                raw_time,
                "%Y%m%d%H%M%S",
            ).replace(tzinfo=SHANGHAI_TZ)
        except ValueError:
            quote_time = None

    error = None
    if current is None or current <= 0 or quote_time is None:
        error = "INVALID_OR_MISSING_FX_QUOTE"

    return FxQuote(
        symbol=symbol,
        current=current,
        quote_time=quote_time,
        source="TENCENT_FX",
        error=error,
    )


def fetch_tencent_fx_quote(
    symbol: str = "whUSDCNY",
    *,
    timeout: int = 8,
) -> FxQuote:
    request = Request(
        f"{TENCENT_QUOTE_URL}{symbol}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("gb18030", errors="replace")
    return parse_tencent_fx_quote(text, symbol=symbol)


def parse_tencent_fx_daily(payload: dict, *, symbol: str) -> list[FxDailyClose]:
    item = (payload.get("data") or {}).get(symbol) or {}
    rows = item.get("day") or []
    result: list[FxDailyClose] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 3:
            continue
        try:
            d = date.fromisoformat(str(row[0]))
        except ValueError:
            continue
        close = _decimal(row[2])
        if close is None or close <= 0:
            continue
        result.append(FxDailyClose(date=d, close=close))
    return result


def fetch_tencent_fx_daily(
    symbol: str = "whUSDCNY",
    *,
    count: int = 30,
    timeout: int = 8,
) -> list[FxDailyClose]:
    params = {"param": f"{symbol},day,,,{count}"}
    request = Request(
        f"{TENCENT_KLINE_URL}?{urlencode(params)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_tencent_fx_daily(payload, symbol=symbol)


def fx_close_on(rows: list[FxDailyClose], target: date) -> Decimal | None:
    for row in rows:
        if row.date == target:
            return row.close
    return None
