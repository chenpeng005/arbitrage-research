from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


SINA_GLOBAL_FUTURES_URL = (
    "https://stock2.finance.sina.com.cn/futures/api/json.php/"
    "GlobalFuturesService.getGlobalFuturesDailyKLine"
)


@dataclass(frozen=True)
class CommodityDailyClose:
    date: date
    close: Decimal


def _decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_sina_global_futures_daily(payload: list[dict]) -> list[CommodityDailyClose]:
    result: list[CommodityDailyClose] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        try:
            d = date.fromisoformat(str(row.get("date") or "")[:10])
        except ValueError:
            continue
        close = _decimal(row.get("close"))
        if close is None or close <= 0:
            continue
        result.append(CommodityDailyClose(date=d, close=close))
    return result


def fetch_sina_global_futures_daily(
    symbol: str,
    *,
    timeout: int = 12,
) -> list[CommodityDailyClose]:
    request = Request(
        f"{SINA_GLOBAL_FUTURES_URL}?{urlencode({'symbol': symbol})}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://finance.sina.com.cn/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, list):
        return []
    return parse_sina_global_futures_daily(payload)


def commodity_close_on(
    rows: list[CommodityDailyClose],
    target: date,
) -> Decimal | None:
    for row in rows:
        if row.date == target:
            return row.close
    return None
