from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .http_json import fetch_json_with_retry


EASTMONEY_DELAYED_LIST_URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class DelayedGlobalIndexQuote:
    secid: str
    code: str | None
    name: str | None
    current: Decimal | None
    previous_close: Decimal | None
    quote_time: datetime | None
    source: str
    error: str | None = None

    @property
    def session_return(self) -> Decimal | None:
        if (
            self.current is None
            or self.previous_close is None
            or self.previous_close <= 0
        ):
            return None
        return self.current / self.previous_close - Decimal("1")


def _decimal(value) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def parse_eastmoney_delayed_global_index(
    payload: dict,
    *,
    secid: str,
) -> DelayedGlobalIndexQuote:
    rows = ((payload.get("data") or {}).get("diff") or [])
    target_code = secid.split(".", 1)[-1]
    row = next(
        (
            item
            for item in rows
            if str(item.get("f12") or "").upper() == target_code.upper()
        ),
        rows[0] if rows else None,
    )
    if not isinstance(row, dict):
        return DelayedGlobalIndexQuote(
            secid=secid,
            code=None,
            name=None,
            current=None,
            previous_close=None,
            quote_time=None,
            source="EASTMONEY_PUSH2DELAY",
            error="NO_QUOTE",
        )

    current = _decimal(row.get("f2"))
    previous_close = _decimal(row.get("f18"))
    quote_time = None
    raw_ts = row.get("f124")
    try:
        if raw_ts is not None and int(raw_ts) > 0:
            quote_time = datetime.fromtimestamp(
                int(raw_ts),
                tz=timezone.utc,
            ).astimezone(SHANGHAI_TZ)
    except (TypeError, ValueError, OSError):
        quote_time = None

    error = None
    if (
        current is None
        or current <= 0
        or previous_close is None
        or previous_close <= 0
        or quote_time is None
    ):
        error = "INVALID_OR_MISSING_DELAYED_INDEX_QUOTE"

    return DelayedGlobalIndexQuote(
        secid=secid,
        code=(str(row.get("f12")).strip() if row.get("f12") else None),
        name=(str(row.get("f14")).strip() if row.get("f14") else None),
        current=current,
        previous_close=previous_close,
        quote_time=quote_time,
        source="EASTMONEY_PUSH2DELAY",
        error=error,
    )


def fetch_eastmoney_delayed_global_index(
    secid: str = "100.SENSEX",
    *,
    timeout: int = 8,
    attempts: int = 4,
) -> DelayedGlobalIndexQuote:
    params = {
        "secids": secid,
        "fltt": "2",
        "invt": "2",
        "fields": "f12,f14,f2,f3,f4,f17,f15,f16,f18,f124",
    }
    payload = fetch_json_with_retry(
        EASTMONEY_DELAYED_LIST_URL,
        params,
        referer="https://quote.eastmoney.com/",
        timeout=timeout,
        attempts=attempts,
        base_delay_seconds=0.35,
    )
    return parse_eastmoney_delayed_global_index(payload, secid=secid)
