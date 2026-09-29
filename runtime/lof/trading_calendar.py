from __future__ import annotations

from datetime import date
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"


def parse_trading_dates(
    payload: dict,
    *,
    symbol: str,
) -> list[date]:
    root = (payload.get("data") or {}).get(symbol) or {}
    rows = root.get("day") or []
    result: list[date] = []
    for row in rows:
        if not row:
            continue
        try:
            result.append(date.fromisoformat(str(row[0])[:10]))
        except ValueError:
            continue
    return sorted(set(result))


def previous_trading_day_from_dates(
    dates: list[date],
    *,
    as_of: date,
) -> date | None:
    candidates = [d for d in dates if d < as_of]
    return max(candidates) if candidates else None


def fetch_previous_trading_day(
    *,
    as_of: date,
    symbol: str = "sh000001",
    timeout: int = 10,
    count: int = 12,
) -> date:
    params = {
        "param": f"{symbol},day,,,{count},qfq",
    }
    request = Request(
        f"{TENCENT_KLINE_URL}?{urlencode(params)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))

    dates = parse_trading_dates(payload, symbol=symbol)
    previous = previous_trading_day_from_dates(dates, as_of=as_of)
    if previous is None:
        raise RuntimeError("PREVIOUS_TRADING_DAY_UNAVAILABLE")
    return previous
