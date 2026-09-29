from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo


EASTMONEY_FUTURES_URL = "https://futsseapi.eastmoney.com/static/{market}_{code}_qt"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class FuturesOverlayQuote:
    code: str
    current: Decimal | None
    previous_settlement: Decimal | None
    pct_change: Decimal | None
    quote_time: datetime | None
    source: str
    error: str | None = None

    @property
    def adjustment_return(self) -> Decimal | None:
        if (
            self.current is None
            or self.previous_settlement is None
            or self.previous_settlement <= 0
        ):
            return None
        return self.current / self.previous_settlement - Decimal("1")


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_eastmoney_global_futures(
    payload: dict,
    *,
    code: str,
) -> FuturesOverlayQuote:
    row = payload.get("qt") or {}
    current = _decimal(row.get("p"))
    previous_settlement = _decimal(row.get("fzjsj"))
    pct_change = _decimal(row.get("zdf"))

    quote_time = None
    raw_date = str(row.get("tjsrq") or "")
    raw_time = str(row.get("jysj") or "").zfill(6)
    if len(raw_date) == 8 and len(raw_time) == 6:
        try:
            quote_time = datetime.strptime(
                raw_date + raw_time,
                "%Y%m%d%H%M%S",
            ).replace(tzinfo=SHANGHAI_TZ)
        except ValueError:
            quote_time = None

    error = None
    if (
        current is None
        or current <= 0
        or previous_settlement is None
        or previous_settlement <= 0
        or quote_time is None
    ):
        error = "INVALID_OR_MISSING_FUTURES_QUOTE"

    return FuturesOverlayQuote(
        code=code,
        current=current,
        previous_settlement=previous_settlement,
        pct_change=pct_change,
        quote_time=quote_time,
        source="EASTMONEY_FUTSSEAPI",
        error=error,
    )


def fetch_eastmoney_global_futures(
    *,
    market: str,
    code: str,
    timeout: int = 8,
) -> FuturesOverlayQuote:
    request = Request(
        EASTMONEY_FUTURES_URL.format(market=market, code=code),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_eastmoney_global_futures(payload, code=code)
