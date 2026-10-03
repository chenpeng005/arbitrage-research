from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re
import time
from typing import Iterable
from urllib.request import Request, urlopen

from .index_quote import IndexQuote, fetch_tencent_index_quotes


CSI_OSS_BASE = (
    "https://oss-ch.csindex.com.cn/static/html/csindex/public/"
    "uploads/file/autofile"
)
CSI_REFERER = "https://www.csindex.com.cn/"
CSI_COMPONENT_SOURCE = "CSI_COMPONENT_WEIGHT_PROXY"


@dataclass(frozen=True)
class CsiWeightedConstituent:
    code: str
    symbol: str
    weight: Decimal


@dataclass(frozen=True)
class CsiComponentWeightSet:
    index_code: str
    index_name: str | None
    weight_date: date | None
    constituent_date: date | None
    constituents: tuple[CsiWeightedConstituent, ...]
    total_weight: Decimal
    weight_url: str | None
    constituent_url: str | None
    fetched_at: datetime
    error: str | None = None

    @property
    def available(self) -> bool:
        return (
            self.error is None
            and self.weight_date is not None
            and bool(self.constituents)
            and self.total_weight > 0
        )


def is_csi_component_proxy_candidate(index_code: str | None) -> bool:
    code = (index_code or "").strip()
    return bool(re.fullmatch(r"(?:9(?:30|31|32|33)\d{3}|H\d{5})", code))


def _date_from_cell(value) -> date | None:
    text = str(value or "").strip()
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def _decimal(value) -> Decimal | None:
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result


def _symbol(code: str, exchange: str) -> str | None:
    text = exchange.strip()
    if "上海" in text or "Shanghai" in text:
        return f"sh{code}"
    if "深圳" in text or "Shenzhen" in text:
        return f"sz{code}"
    if "北京" in text or "Beijing" in text:
        return f"bj{code}"
    return None


def parse_csi_material_urls(payload: dict) -> tuple[str | None, str | None]:
    data = payload.get("data") or {}
    weight_rows = data.get("样本权重") or []
    constituent_rows = data.get("样本列表") or []
    weight_url = (
        str(weight_rows[0].get("filePath") or "").strip()
        if weight_rows
        else ""
    )
    constituent_url = (
        str(constituent_rows[0].get("filePath") or "").strip()
        if constituent_rows
        else ""
    )
    return weight_url or None, constituent_url or None


def _fetch_bytes(
    url: str,
    *,
    timeout: int,
    attempts: int = 3,
) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(max(1, attempts)):
        try:
            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": CSI_REFERER,
                },
            )
            with urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:
            last_exc = exc
            if attempt + 1 >= max(1, attempts):
                raise
            time.sleep(0.25 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


def _read_xls_rows(content: bytes) -> list[list]:
    try:
        import xlrd
    except ImportError as exc:
        raise RuntimeError("XLRD_NOT_INSTALLED") from exc
    book = xlrd.open_workbook(file_contents=content)
    sheet = book.sheet_by_index(0)
    return [sheet.row_values(i) for i in range(sheet.nrows)]


def _build_weight_set(
    *,
    index_code: str,
    weight_rows: list[list],
    constituent_rows: list[list],
    weight_url: str,
    constituent_url: str,
    fetched_at: datetime,
) -> CsiComponentWeightSet:
    if len(weight_rows) < 2 or len(constituent_rows) < 2:
        return CsiComponentWeightSet(
            index_code=index_code,
            index_name=None,
            weight_date=None,
            constituent_date=None,
            constituents=(),
            total_weight=Decimal("0"),
            weight_url=weight_url,
            constituent_url=constituent_url,
            fetched_at=fetched_at,
            error="EMPTY_CSI_MATERIAL",
        )

    weight_date = _date_from_cell(weight_rows[1][0])
    constituent_date = _date_from_cell(constituent_rows[1][0])
    index_name = str(weight_rows[1][2] or "").strip() or None

    current_codes = {
        str(row[4]).split(".")[0].strip().zfill(6)
        for row in constituent_rows[1:]
        if len(row) > 4 and str(row[4]).strip()
    }

    weighted: list[CsiWeightedConstituent] = []
    weight_codes: set[str] = set()
    for row in weight_rows[1:]:
        if len(row) <= 9:
            continue
        code = str(row[4]).split(".")[0].strip().zfill(6)
        if not code:
            continue
        weight = _decimal(row[9])
        symbol = _symbol(code, str(row[7] or ""))
        if weight is None or weight <= 0 or symbol is None:
            continue
        weight_codes.add(code)
        weighted.append(
            CsiWeightedConstituent(
                code=code,
                symbol=symbol,
                weight=weight,
            )
        )

    total_weight = sum((row.weight for row in weighted), Decimal("0"))
    error = None
    if weight_date is None or constituent_date is None:
        error = "INVALID_CSI_MATERIAL_DATE"
    elif current_codes != weight_codes:
        error = "CSI_CONSTITUENT_WEIGHT_SET_MISMATCH"
    elif not Decimal("98") <= total_weight <= Decimal("102"):
        error = "INVALID_CSI_WEIGHT_SUM"
    elif not weighted:
        error = "EMPTY_CSI_WEIGHT_ROWS"

    return CsiComponentWeightSet(
        index_code=index_code,
        index_name=index_name,
        weight_date=weight_date,
        constituent_date=constituent_date,
        constituents=tuple(weighted),
        total_weight=total_weight,
        weight_url=weight_url,
        constituent_url=constituent_url,
        fetched_at=fetched_at,
        error=error,
    )


def fetch_csi_component_weight_set(
    index_code: str,
    *,
    timeout: int = 15,
    fetched_at: datetime | None = None,
) -> CsiComponentWeightSet:
    now = fetched_at or datetime.now().astimezone()
    code = index_code.strip()
    if not is_csi_component_proxy_candidate(code):
        return CsiComponentWeightSet(
            index_code=code,
            index_name=None,
            weight_date=None,
            constituent_date=None,
            constituents=(),
            total_weight=Decimal("0"),
            weight_url=None,
            constituent_url=None,
            fetched_at=now,
            error="UNSUPPORTED_CSI_COMPONENT_INDEX_CODE",
        )

    try:
        weight_url = (
            f"{CSI_OSS_BASE}/closeweight/{code}closeweight.xls"
        )
        constituent_url = f"{CSI_OSS_BASE}/cons/{code}cons.xls"
        weight_content = _fetch_bytes(weight_url, timeout=timeout)
        constituent_content = _fetch_bytes(constituent_url, timeout=timeout)
        return _build_weight_set(
            index_code=code,
            weight_rows=_read_xls_rows(weight_content),
            constituent_rows=_read_xls_rows(constituent_content),
            weight_url=weight_url,
            constituent_url=constituent_url,
            fetched_at=now,
        )
    except Exception as exc:
        return CsiComponentWeightSet(
            index_code=code,
            index_name=None,
            weight_date=None,
            constituent_date=None,
            constituents=(),
            total_weight=Decimal("0"),
            weight_url=None,
            constituent_url=None,
            fetched_at=now,
            error=f"CSI_COMPONENT_CONTEXT_ERROR:{type(exc).__name__}",
        )


def fetch_csi_component_weight_sets(
    index_codes: Iterable[str],
    *,
    timeout: int = 15,
    max_workers: int = 6,
    fetched_at: datetime | None = None,
) -> dict[str, CsiComponentWeightSet]:
    codes = sorted(
        {
            str(code).strip()
            for code in index_codes
            if is_csi_component_proxy_candidate(str(code).strip())
        }
    )
    if not codes:
        return {}

    result: dict[str, CsiComponentWeightSet] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(
                fetch_csi_component_weight_set,
                code,
                timeout=timeout,
                fetched_at=fetched_at,
            ): code
            for code in codes
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                result[code] = future.result()
            except Exception as exc:
                now = fetched_at or datetime.now().astimezone()
                result[code] = CsiComponentWeightSet(
                    index_code=code,
                    index_name=None,
                    weight_date=None,
                    constituent_date=None,
                    constituents=(),
                    total_weight=Decimal("0"),
                    weight_url=None,
                    constituent_url=None,
                    fetched_at=now,
                    error=f"CSI_COMPONENT_CONTEXT_ERROR:{type(exc).__name__}",
                )
    return result


def reconstruct_csi_component_quotes(
    weight_sets: dict[str, CsiComponentWeightSet],
    *,
    as_of: datetime,
    timeout: int = 10,
    min_weight_coverage: Decimal = Decimal("0.99"),
    max_weight_age_days: int = 45,
) -> dict[str, IndexQuote]:
    valid_sets = {
        code: row
        for code, row in weight_sets.items()
        if row.available
    }
    all_symbols = sorted(
        {
            constituent.symbol
            for row in valid_sets.values()
            for constituent in row.constituents
        }
    )
    stock_quotes = fetch_tencent_index_quotes(
        all_symbols,
        timeout=timeout,
    )

    result: dict[str, IndexQuote] = {}
    for code, row in weight_sets.items():
        symbol = f"CSI_WEIGHT_{code}"
        if not row.available:
            result[code] = IndexQuote(
                symbol=symbol,
                code=code,
                name=row.index_name,
                current=None,
                previous_close=None,
                quote_time=None,
                source=CSI_COMPONENT_SOURCE,
                error=row.error or "CSI_COMPONENT_CONTEXT_UNAVAILABLE",
            )
            continue

        assert row.weight_date is not None
        weight_age = (as_of.date() - row.weight_date).days
        if weight_age < 0 or weight_age > max_weight_age_days:
            result[code] = IndexQuote(
                symbol=symbol,
                code=code,
                name=row.index_name,
                current=None,
                previous_close=None,
                quote_time=None,
                source=CSI_COMPONENT_SOURCE,
                error="CSI_COMPONENT_WEIGHT_STALE",
            )
            continue

        covered = Decimal("0")
        weighted_return = Decimal("0")
        quote_times: list[datetime] = []
        for constituent in row.constituents:
            quote = stock_quotes.get(constituent.symbol)
            if (
                quote is None
                or quote.error is not None
                or quote.current is None
                or quote.previous_close is None
                or quote.current <= 0
                or quote.previous_close <= 0
                or quote.quote_time is None
            ):
                continue
            covered += constituent.weight
            quote_times.append(quote.quote_time)
            weighted_return += constituent.weight * (
                quote.current / quote.previous_close - Decimal("1")
            )

        coverage = (
            covered / row.total_weight
            if row.total_weight > 0
            else Decimal("0")
        )
        if coverage < min_weight_coverage or covered <= 0:
            result[code] = IndexQuote(
                symbol=symbol,
                code=code,
                name=row.index_name,
                current=None,
                previous_close=None,
                quote_time=None,
                source=CSI_COMPONENT_SOURCE,
                error="CSI_COMPONENT_QUOTE_COVERAGE_TOO_LOW",
            )
            continue

        proxy_return = weighted_return / covered
        # Freshness belongs to the underlying market data, not to the
        # reconstruction clock. Use the oldest included constituent quote so
        # weekends/holidays cannot become "fresh" merely because we recompute.
        component_quote_time = min(quote_times)
        result[code] = IndexQuote(
            symbol=symbol,
            code=code,
            name=row.index_name,
            current=Decimal("1") + proxy_return,
            previous_close=Decimal("1"),
            quote_time=component_quote_time,
            source=CSI_COMPONENT_SOURCE,
            error=None,
        )

    return result