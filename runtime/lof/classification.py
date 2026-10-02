from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Iterable
from urllib.request import Request, urlopen

from .universe import LofIdentity


EASTMONEY_FUND_CODE_URL = "https://fund.eastmoney.com/js/fundcode_search.js"


@dataclass(frozen=True)
class FundTypeRecord:
    code: str
    name_raw: str | None
    fund_type_raw: str | None
    lof_type: str
    source: str
    error: str | None = None


def normalize_lof_type(
    *,
    name: str | None,
    fund_type_raw: str | None,
) -> str:
    name_text = (name or "").upper()
    type_text = (fund_type_raw or "").upper()
    joined = f"{name_text} {type_text}"

    is_qdii = (
        "QDII" in joined
        or "海外" in (fund_type_raw or "")
    )

    if is_qdii and "商品" in (fund_type_raw or ""):
        return "QDII_COMMODITY"
    if is_qdii:
        return "QDII_EQUITY"
    if "商品" in (fund_type_raw or ""):
        return "COMMODITY"
    if (
        "债券" in (fund_type_raw or "")
        or "固收" in (fund_type_raw or "")
    ):
        return "BOND"
    if "FOF" in joined:
        return "FOF"
    if "混合" in (fund_type_raw or ""):
        return "MIXED"
    if (
        "股票" in (fund_type_raw or "")
        or "指数型-股票" in (fund_type_raw or "")
    ):
        return "EQUITY"
    return "OTHER"


def parse_fundcode_search_js(text: str) -> dict[str, FundTypeRecord]:
    match = re.search(r"=\s*(\[.*\])\s*;?\s*$", text, re.S)
    if not match:
        raise ValueError("fundcode_search.js payload not recognized")

    rows = json.loads(match.group(1))
    result: dict[str, FundTypeRecord] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 4:
            continue
        code = str(row[0] or "").strip()
        if not code:
            continue
        name = str(row[2] or "").strip() or None
        raw_type = str(row[3] or "").strip() or None
        result[code] = FundTypeRecord(
            code=code,
            name_raw=name,
            fund_type_raw=raw_type,
            lof_type=normalize_lof_type(
                name=name,
                fund_type_raw=raw_type,
            ),
            source="EASTMONEY_FUND_CODE",
            error=None,
        )
    return result


def fetch_fund_type_map(*, timeout: int = 20) -> dict[str, FundTypeRecord]:
    request = Request(
        EASTMONEY_FUND_CODE_URL,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()

    for encoding in ("utf-8", "gb18030", "gbk"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("latin1", errors="replace")

    return parse_fundcode_search_js(text)


def classify_universe(
    universe: Iterable[LofIdentity],
    *,
    type_map: dict[str, FundTypeRecord],
) -> dict[tuple[str, str], FundTypeRecord]:
    result: dict[tuple[str, str], FundTypeRecord] = {}
    for item in universe:
        record = type_map.get(item.code)
        if record is None:
            record = FundTypeRecord(
                code=item.code,
                name_raw=item.name,
                fund_type_raw=None,
                lof_type="OTHER",
                source="EASTMONEY_FUND_CODE",
                error="MISSING_TYPE_DATA",
            )
        result[(item.exchange, item.code)] = record
    return result
