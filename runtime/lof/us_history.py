from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen


TENCENT_US_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/usfqkline/get"


@dataclass(frozen=True)
class DailyClose:
    date: date
    close: Decimal


def _decimal(value) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_tencent_us_daily(payload: dict, *, key: str) -> list[DailyClose]:
    item = (payload.get("data") or {}).get(key) or {}
    rows = item.get("qfqday") or item.get("day") or []
    result: list[DailyClose] = []
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
        result.append(DailyClose(date=d, close=close))
    return result


def fetch_tencent_us_daily(
    *,
    symbol_with_exchange: str,
    count: int = 30,
    timeout: int = 10,
) -> list[DailyClose]:
    key = f"us{symbol_with_exchange}"
    params = {
        "param": f"{key},day,,,{count},qfq",
    }
    request = Request(
        f"{TENCENT_US_KLINE_URL}?{urlencode(params)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_tencent_us_daily(payload, key=key)


def close_on(rows: list[DailyClose], target: date) -> Decimal | None:
    for row in rows:
        if row.date == target:
            return row.close
    return None


def latest_close(rows: list[DailyClose]) -> DailyClose | None:
    return max(rows, key=lambda x: x.date) if rows else None
