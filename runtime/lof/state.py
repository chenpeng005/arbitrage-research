from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from typing import Any, Iterable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .universe import LofIdentity


EASTMONEY_RATE_URL = "https://fundmobapi.eastmoney.com/FundMApi/FundRateInfo.ashx"


@dataclass(frozen=True)
class FundTradeStateRecord:
    code: str
    subscription_status: str
    subscription_status_raw: str | None
    redemption_status: str
    redemption_status_raw: str | None
    daily_subscription_limit: Decimal | None
    minimum_subscription_amount: Decimal | None
    limit_scope: str
    subscription_confirmation_days: int | None
    subscription_to_sell_days: int | None
    subscription_fee_schedule: tuple[dict[str, Any], ...]
    redemption_fee_schedule: tuple[dict[str, Any], ...]
    fee_source: str
    state_source: str
    fetched_at: datetime
    error: str | None = None


def _decimal_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"--", "-", "不限", "无限额"}:
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return number if number >= 0 else None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or not text.lstrip("-").isdigit():
        return None
    value_int = int(text)
    return value_int if value_int >= 0 else None


def normalize_subscription_status(raw: str | None) -> str:
    text = (raw or "").strip()
    if text == "开放申购":
        return "OPEN"
    if text in {"限大额", "暂停大额申购"}:
        return "LIMITED"
    if text in {"暂停申购", "封闭期"}:
        return "SUSPENDED"
    return "UNKNOWN"


def normalize_redemption_status(raw: str | None) -> str:
    text = (raw or "").strip()
    if text == "开放赎回":
        return "OPEN"
    if text in {"暂停赎回", "封闭期"}:
        return "SUSPENDED"
    return "UNKNOWN"


def parse_eastmoney_trade_state(
    payload: dict[str, Any],
    *,
    code: str,
    fetched_at: datetime | None = None,
) -> FundTradeStateRecord:
    fetched_at = fetched_at or datetime.now(timezone.utc)
    data = payload.get("Datas") or payload.get("data")

    if not isinstance(data, dict):
        return FundTradeStateRecord(
            code=code,
            subscription_status="UNKNOWN",
            subscription_status_raw=None,
            redemption_status="UNKNOWN",
            redemption_status_raw=None,
            daily_subscription_limit=None,
            minimum_subscription_amount=None,
            limit_scope="UNKNOWN",
            subscription_confirmation_days=None,
            subscription_to_sell_days=None,
            subscription_fee_schedule=(),
            redemption_fee_schedule=(),
            fee_source="EASTMONEY_CHANNEL_REFERENCE",
            state_source="EASTMONEY_FUND_MOBILE",
            fetched_at=fetched_at,
            error="NO_STATE_DATA",
        )

    subscription_raw = str(data.get("SGZT") or "").strip() or None
    redemption_raw = str(data.get("SHZT") or "").strip() or None

    sg = data.get("sg")
    sh = data.get("sh")
    subscription_fee_schedule = tuple(sg) if isinstance(sg, list) else ()
    redemption_fee_schedule = tuple(sh) if isinstance(sh, list) else ()

    return FundTradeStateRecord(
        code=code,
        subscription_status=normalize_subscription_status(subscription_raw),
        subscription_status_raw=subscription_raw,
        redemption_status=normalize_redemption_status(redemption_raw),
        redemption_status_raw=redemption_raw,
        daily_subscription_limit=_decimal_or_none(data.get("MAXSG")),
        minimum_subscription_amount=_decimal_or_none(data.get("MINSG")),
        # This source does not prove whether the limit is per investor,
        # per fund account, or channel-specific.
        limit_scope="UNKNOWN",
        subscription_confirmation_days=_int_or_none(data.get("SSBCFMDATA")),
        # Confirmation T+N is NOT the same as the first sellable day.
        subscription_to_sell_days=None,
        subscription_fee_schedule=subscription_fee_schedule,
        redemption_fee_schedule=redemption_fee_schedule,
        fee_source="EASTMONEY_CHANNEL_REFERENCE",
        state_source="EASTMONEY_FUND_MOBILE",
        fetched_at=fetched_at,
        error=None,
    )


def _fetch_json(code: str, *, timeout: int) -> dict[str, Any]:
    params = {
        "FCODE": code,
        "deviceid": "1",
        "plat": "Android",
        "product": "EFund",
        "version": "6.5.5",
    }
    request = Request(
        f"{EASTMONEY_RATE_URL}?{urlencode(params)}",
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json,text/plain,*/*",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_trade_state_for_code(
    code: str,
    *,
    timeout: int = 15,
) -> FundTradeStateRecord:
    payload = _fetch_json(code, timeout=timeout)
    return parse_eastmoney_trade_state(payload, code=code)


def fetch_all_trade_states(
    universe: Iterable[LofIdentity],
    *,
    timeout: int = 15,
    max_workers: int = 8,
) -> list[FundTradeStateRecord]:
    codes = sorted({row.code for row in universe})
    if not codes:
        return []

    result: dict[str, FundTradeStateRecord] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_map = {
            pool.submit(fetch_trade_state_for_code, code, timeout=timeout): code
            for code in codes
        }
        for future in as_completed(future_map):
            code = future_map[future]
            try:
                result[code] = future.result()
            except Exception as exc:
                result[code] = FundTradeStateRecord(
                    code=code,
                    subscription_status="UNKNOWN",
                    subscription_status_raw=None,
                    redemption_status="UNKNOWN",
                    redemption_status_raw=None,
                    daily_subscription_limit=None,
                    minimum_subscription_amount=None,
                    limit_scope="UNKNOWN",
                    subscription_confirmation_days=None,
                    subscription_to_sell_days=None,
                    subscription_fee_schedule=(),
                    redemption_fee_schedule=(),
                    fee_source="EASTMONEY_CHANNEL_REFERENCE",
                    state_source="EASTMONEY_FUND_MOBILE",
                    fetched_at=datetime.now(timezone.utc),
                    error=f"FETCH_ERROR:{type(exc).__name__}",
                )

    return [result[code] for code in codes]
