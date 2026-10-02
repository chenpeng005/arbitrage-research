from __future__ import annotations

from datetime import date, datetime
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TENCENT_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="


def parse_trading_dates(
    payload: dict,
    *,
    symbol: str,
) -> list[date]:
    root = (payload.get("data") or {}).get(symbol) or {}
    rows = root.get("qfqday") or root.get("day") or []
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




def parse_last_market_trade_date(
    text: str,
    *,
    symbol: str,
) -> date | None:
    marker = f'v_{symbol}="'
    start = text.find(marker)
    if start < 0:
        return None
    body = text[start + len(marker):].split('"', 1)[0]
    fields = body.split("~")
    if len(fields) <= 30:
        return None
    raw_time = fields[30].strip()
    if len(raw_time) < 8 or not raw_time[:8].isdigit():
        return None
    try:
        return datetime.strptime(raw_time[:8], "%Y%m%d").date()
    except ValueError:
        return None


def fetch_last_market_trade_date(
    *,
    symbol: str = "sh000001",
    timeout: int = 8,
) -> date | None:
    request = Request(
        f"{TENCENT_QUOTE_URL}{symbol}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("gb18030", errors="replace")
    return parse_last_market_trade_date(text, symbol=symbol)


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
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        dates = parse_trading_dates(payload, symbol=symbol)
        previous = previous_trading_day_from_dates(dates, as_of=as_of)
        if previous is not None:
            return previous
    except Exception:
        # Holiday / off-hours fallback below. Do not guess by subtracting
        # calendar days because long holidays would be wrong.
        pass

    try:
        last_trade_date = fetch_last_market_trade_date(
            symbol=symbol,
            timeout=timeout,
        )
    except Exception:
        last_trade_date = None

    # This fallback is safe only when the quote itself proves that the most
    # recent market trade date is strictly before as_of. During an active
    # trading day the quote date may equal as_of, which does not reveal the
    # previous session date, so we fail closed.
    if last_trade_date is not None and last_trade_date < as_of:
        return last_trade_date

    raise RuntimeError("PREVIOUS_TRADING_DAY_UNAVAILABLE")
