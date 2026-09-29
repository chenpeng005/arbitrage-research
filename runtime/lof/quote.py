from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Iterable
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .universe import LofIdentity


TENCENT_QUOTE_URL = "https://qt.gtimg.cn/q="
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class QuoteRecord:
    code: str
    exchange: str
    name: str | None
    price: Decimal | None
    quote_time: datetime | None
    pct_change: Decimal | None
    volume: Decimal | None
    amount: Decimal | None
    source: str
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.price is not None and self.quote_time is not None and self.error is None


def _decimal_or_none(value: str | None) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _parse_quote_time(value: str | None) -> datetime | None:
    text = (value or "").strip()
    if len(text) != 14 or not text.isdigit():
        return None
    try:
        naive = datetime.strptime(text, "%Y%m%d%H%M%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=SHANGHAI_TZ)


def _exchange_prefix(exchange: str) -> str:
    if exchange == "SSE":
        return "sh"
    if exchange == "SZSE":
        return "sz"
    raise ValueError(f"unsupported exchange: {exchange}")


def _extract_exact_amount(fields: list[str]) -> Decimal | None:
    # Tencent field 35 is typically "price/volume/amount".
    if len(fields) <= 35:
        return None
    parts = fields[35].split("/")
    if len(parts) < 3:
        return None
    return _decimal_or_none(parts[2])


def parse_tencent_quote_line(line: str) -> QuoteRecord | None:
    text = line.strip()
    if not text or '="' not in text:
        return None

    prefix, body = text.split('="', 1)
    body = body.rsplit('"', 1)[0]
    fields = body.split("~")
    if len(fields) < 36:
        return None

    code = fields[2].strip()
    if not code:
        return None

    exchange = "SSE" if "sh" in prefix.lower() else "SZSE"
    price = _decimal_or_none(fields[3])
    quote_time = _parse_quote_time(fields[30] if len(fields) > 30 else None)
    pct_change = _decimal_or_none(fields[32] if len(fields) > 32 else None)
    volume = _decimal_or_none(fields[36] if len(fields) > 36 else None)
    amount = _extract_exact_amount(fields)

    error = None
    if price is None or quote_time is None:
        error = "INVALID_OR_MISSING_QUOTE"

    return QuoteRecord(
        code=code,
        exchange=exchange,
        name=(fields[1].strip() or None),
        price=price,
        quote_time=quote_time,
        pct_change=pct_change,
        volume=volume,
        amount=amount,
        source="TENCENT_QUOTE",
        error=error,
    )


def parse_tencent_quote_response(text: str) -> list[QuoteRecord]:
    result: list[QuoteRecord] = []
    for line in text.split(";"):
        record = parse_tencent_quote_line(line)
        if record is not None:
            result.append(record)
    return result


def _fetch_text(url: str, *, timeout: int) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://gu.qq.com/",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()

    # Tencent quote text is commonly GBK/GB18030.
    for encoding in ("gb18030", "gbk", "utf-8"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin1", errors="replace")


def fetch_quotes(
    universe: Iterable[LofIdentity],
    *,
    timeout: int = 10,
    batch_size: int = 60,
) -> list[QuoteRecord]:
    rows = list(universe)
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")

    requested: dict[tuple[str, str], LofIdentity] = {
        (row.exchange, row.code): row for row in rows
    }
    found: dict[tuple[str, str], QuoteRecord] = {}

    for start in range(0, len(rows), batch_size):
        batch = rows[start : start + batch_size]
        query_codes = ",".join(
            f"{_exchange_prefix(row.exchange)}{row.code}" for row in batch
        )
        try:
            text = _fetch_text(
                f"{TENCENT_QUOTE_URL}{query_codes}",
                timeout=timeout,
            )
            for record in parse_tencent_quote_response(text):
                key = (record.exchange, record.code)
                if key in requested:
                    found[key] = record
        except Exception as exc:
            error_name = f"FETCH_ERROR:{type(exc).__name__}"
            for row in batch:
                key = (row.exchange, row.code)
                found[key] = QuoteRecord(
                    code=row.code,
                    exchange=row.exchange,
                    name=row.name,
                    price=None,
                    quote_time=None,
                    pct_change=None,
                    volume=None,
                    amount=None,
                    source="TENCENT_QUOTE",
                    error=error_name,
                )

    result: list[QuoteRecord] = []
    for row in rows:
        key = (row.exchange, row.code)
        record = found.get(key)
        if record is None:
            record = QuoteRecord(
                code=row.code,
                exchange=row.exchange,
                name=row.name,
                price=None,
                quote_time=None,
                pct_change=None,
                volume=None,
                amount=None,
                source="TENCENT_QUOTE",
                error="MISSING_FROM_QUOTE_SOURCE",
            )
        result.append(record)

    return sorted(result, key=lambda item: (item.exchange, item.code))


def quote_age_seconds(record: QuoteRecord, *, now: datetime) -> int | None:
    if record.quote_time is None:
        return None

    quote_time = record.quote_time
    current = now

    if quote_time.tzinfo is None:
        quote_time = quote_time.replace(tzinfo=SHANGHAI_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=SHANGHAI_TZ)

    return max(0, int((current.astimezone(SHANGHAI_TZ) - quote_time).total_seconds()))


def is_quote_stale(
    record: QuoteRecord,
    *,
    now: datetime,
    max_age_seconds: int,
) -> bool:
    age = quote_age_seconds(record, now=now)
    return age is None or age > max_age_seconds
