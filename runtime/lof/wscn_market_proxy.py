from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from .http_json import fetch_json_with_retry


WSCN_MARKET_REAL_URL = "https://api-ddc-wscn.awtmt.com/market/real"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class WscnMarketProxyQuote:
    prod_code: str
    symbol: str | None
    name: str | None
    current: Decimal | None
    previous_close: Decimal | None
    quote_time: datetime | None
    source: str
    error: str | None = None

    @property
    def return_from_previous_close(self) -> Decimal | None:
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
        raw = int(value)
    except (TypeError, ValueError):
        return None
    if raw <= 0:
        return None
    try:
        return datetime.fromtimestamp(
            raw,
            tz=timezone.utc,
        ).astimezone(SHANGHAI_TZ)
    except (ValueError, OSError):
        return None


def parse_wscn_market_proxy(
    payload: dict,
    *,
    prod_code: str,
) -> WscnMarketProxyQuote:
    data = payload.get("data") or {}
    fields = data.get("fields") or []
    values = (data.get("snapshot") or {}).get(prod_code)
    if not fields or not isinstance(values, list):
        return WscnMarketProxyQuote(
            prod_code=prod_code,
            symbol=None,
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
        error = "INVALID_OR_MISSING_MARKET_PROXY"

    return WscnMarketProxyQuote(
        prod_code=prod_code,
        symbol=(str(row.get("symbol")).strip() if row.get("symbol") else None),
        name=(str(row.get("prod_name")).strip() if row.get("prod_name") else None),
        current=current,
        previous_close=previous_close,
        quote_time=quote_time,
        source="WSCN_MARKET_REAL",
        error=error,
    )


def fetch_wscn_market_proxy(
    prod_code: str,
    *,
    timeout: int = 8,
    attempts: int = 3,
) -> WscnMarketProxyQuote:
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
    return parse_wscn_market_proxy(payload, prod_code=prod_code)
