from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .http_json import fetch_json_with_retry


EASTMONEY_DELAYED_LIST_URL = "https://push2delay.eastmoney.com/api/qt/ulist.np/get"
WSCN_MARKET_REAL_URL = "https://api-ddc-wscn.awtmt.com/market/real"
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


def _quote_time(value) -> datetime | None:
    try:
        if value is None or int(value) <= 0:
            return None
        return datetime.fromtimestamp(
            int(value),
            tz=timezone.utc,
        ).astimezone(SHANGHAI_TZ)
    except (TypeError, ValueError, OSError):
        return None


def parse_wscn_delayed_global_index(
    payload: dict,
    *,
    prod_code: str = "SENSEX.OTC",
) -> DelayedGlobalIndexQuote:
    data = payload.get("data") or {}
    fields = data.get("fields") or []
    values = (data.get("snapshot") or {}).get(prod_code)
    if not isinstance(values, list) or not fields:
        return DelayedGlobalIndexQuote(
            secid=prod_code,
            code=None,
            name=None,
            current=None,
            previous_close=None,
            quote_time=None,
            source="WSCN_MARKET_REAL",
            error="NO_QUOTE",
        )

    row = dict(zip(fields, values))
    current = _decimal(row.get("last_px"))
    previous_close = _decimal(row.get("preclose_px"))
    if previous_close is None:
        change = _decimal(row.get("px_change"))
        if current is not None and change is not None:
            previous_close = current - change
    quote_time = _quote_time(row.get("update_time"))
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
        secid=prod_code,
        code=(str(row.get("symbol")).strip() if row.get("symbol") else None),
        name=(str(row.get("prod_name")).strip() if row.get("prod_name") else None),
        current=current,
        previous_close=previous_close,
        quote_time=quote_time,
        source="WSCN_MARKET_REAL",
        error=error,
    )


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
    quote_time = _quote_time(row.get("f124"))
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


def fetch_wscn_delayed_sensex(
    *,
    timeout: int = 8,
    attempts: int = 3,
) -> DelayedGlobalIndexQuote:
    prod_code = "SENSEX.OTC"
    params = {
        "fields": (
            "symbol,en_name,prod_name,last_px,px_change,px_change_rate,"
            "update_time,preclose_px,open_px,high_px,low_px"
        ),
        "prod_code": prod_code,
    }
    payload = fetch_json_with_retry(
        WSCN_MARKET_REAL_URL,
        params,
        timeout=timeout,
        attempts=attempts,
        base_delay_seconds=0.25,
    )
    return parse_wscn_delayed_global_index(payload, prod_code=prod_code)


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


def fetch_india_sensex_quote(
    *,
    timeout: int = 8,
) -> DelayedGlobalIndexQuote:
    errors: list[str] = []
    try:
        quote = fetch_wscn_delayed_sensex(timeout=timeout)
        if quote.error is None:
            return quote
        errors.append(f"WSCN:{quote.error}")
    except Exception as exc:
        errors.append(f"WSCN:{type(exc).__name__}")

    try:
        quote = fetch_eastmoney_delayed_global_index(timeout=timeout)
        if quote.error is None:
            return quote
        errors.append(f"EASTMONEY:{quote.error}")
    except Exception as exc:
        errors.append(f"EASTMONEY:{type(exc).__name__}")

    return DelayedGlobalIndexQuote(
        secid="SENSEX.OTC",
        code="SENSEX",
        name="印度孟买SENSEX",
        current=None,
        previous_close=None,
        quote_time=None,
        source="WSCN_THEN_EASTMONEY",
        error="ALL_SENSEX_SOURCES_FAILED:" + "|".join(errors),
    )
