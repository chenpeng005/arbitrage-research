from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import re


EASTMONEY_SUGGEST_URL = "https://searchapi.eastmoney.com/api/suggest/get"
EASTMONEY_SUGGEST_TOKEN = "D43BF722C8E33BDC906FB84D85E326E8"


@dataclass(frozen=True)
class IndexProxyMapping:
    tracking_target_name: str
    index_code: str | None
    index_name: str | None
    quote_id: str | None
    market_num: str | None
    tencent_symbol: str | None
    source: str
    status: str
    error: str | None = None


def _normalize_index_name(value: str | None) -> str:
    text = (value or "").strip()
    text = re.sub(r"\s+", "", text)
    text = text.replace("（", "(").replace("）", ")")
    if text.endswith("指数"):
        text = text[:-2]
    return text.upper()


def _tencent_symbol(code: str, market_num: str | None) -> str | None:
    if market_num == "1":
        return f"sh{code}"
    if market_num == "0":
        return f"sz{code}"
    return None


def parse_index_suggest_payload(
    payload: dict[str, Any],
    *,
    tracking_target_name: str,
) -> IndexProxyMapping:
    data = ((payload.get("QuotationCodeTable") or {}).get("Data") or [])
    target = _normalize_index_name(tracking_target_name)

    candidates: list[dict[str, Any]] = []
    for row in data:
        if row.get("SecurityTypeName") != "指数":
            continue
        name = str(row.get("Name") or "")
        norm = _normalize_index_name(name)
        if norm == target:
            candidates.append(row)

    if not candidates:
        return IndexProxyMapping(
            tracking_target_name=tracking_target_name,
            index_code=None,
            index_name=None,
            quote_id=None,
            market_num=None,
            tencent_symbol=None,
            source="EASTMONEY_SUGGEST",
            status="UNRESOLVED",
            error="NO_EXACT_INDEX_MATCH",
        )

    # Prefer Shanghai-published canonical CSI codes when identical names have
    # both SSE and SZSE publication codes (e.g. 000300 vs 399300).
    candidates.sort(
        key=lambda row: (
            0 if str(row.get("MktNum")) == "1" else 1,
            str(row.get("Code") or ""),
        )
    )
    row = candidates[0]
    code = str(row.get("Code") or "").strip()
    market_num = str(row.get("MktNum") or "").strip() or None

    return IndexProxyMapping(
        tracking_target_name=tracking_target_name,
        index_code=code or None,
        index_name=str(row.get("Name") or "").strip() or None,
        quote_id=str(row.get("QuoteID") or "").strip() or None,
        market_num=market_num,
        tencent_symbol=_tencent_symbol(code, market_num) if code else None,
        source="EASTMONEY_SUGGEST",
        status="RESOLVED",
        error=None,
    )


def fetch_index_proxy_mapping(
    tracking_target_name: str,
    *,
    timeout: int = 12,
) -> IndexProxyMapping:
    params = {
        "input": tracking_target_name,
        "type": "14",
        "token": EASTMONEY_SUGGEST_TOKEN,
        "count": 100,
    }
    request = Request(
        f"{EASTMONEY_SUGGEST_URL}?{urlencode(params)}",
        headers={"User-Agent": "Mozilla/5.0"},
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return parse_index_suggest_payload(
        payload,
        tracking_target_name=tracking_target_name,
    )
