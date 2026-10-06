from __future__ import annotations

from datetime import date, datetime, timezone
import xml.etree.ElementTree as ET
from typing import Any

from .http import fetch_json, fetch_text
from .models import PcfSnapshot


SSE_QUERY_API = "https://query.sse.com.cn/commonQuery.do"
SSE_PCF_PAGE = "https://www.sse.com.cn/disclosure/fund/etflist/"
SSE_PCF_BASIC_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C"

SZSE_PCF_PAGE = "https://www.szse.cn/disclosure/fund/currency/index.html"
SZSE_PCF_XML = "https://reportdocs.static.szse.cn/files/text/ETFDown/pcf_{code}_{day}.xml"


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("￥", "").replace("元", "")
    if not text or text in {"-", "--", "无", "不设上限", "None", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_positive_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None or number <= 0:
        # PCF uses blank/zero for many "not set" limit fields. Treat these as
        # unknown/unlimited instead of creating a false zero-capacity alert.
        return None
    return int(number)


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    lower = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value not in (None, ""):
            return value
    return None


def _parse_sse_switch(value: Any) -> tuple[bool | None, bool | None]:
    text = str(value or "").strip()
    if text == "1":
        return True, True
    if text == "2":
        return True, False
    if text == "3":
        return False, True
    if text == "0":
        return False, False
    if "申购和赎回皆允许" in text or "申购赎回皆允许" in text:
        return True, True
    if "仅允许申购" in text:
        return True, False
    if "仅允许赎回" in text:
        return False, True
    if "禁止" in text:
        return False, False
    return None, None


def parse_sse_basic_row(
    code: str,
    row: dict[str, Any],
    *,
    source_url: str | None = None,
    fetched_at: str | None = None,
) -> PcfSnapshot:
    # SSE's live query currently returns upper-snake-case field names, while
    # downloaded/newer PCF formats use CamelCase. Support both and preserve raw.
    creation_allowed, redemption_allowed = _parse_sse_switch(
        _pick(
            row,
            "CREATION_REDEMPTION",
            "CreationRedemptionSwitch",
            "CREATION_REDEMPTION_SWITCH",
        )
    )
    return PcfSnapshot(
        code=code,
        exchange="SSE",
        trade_date=str(_pick(row, "TRADING_DAY", "TradingDay") or "") or None,
        creation_allowed=creation_allowed,
        redemption_allowed=redemption_allowed,
        creation_redemption_unit=_to_positive_int(
            _pick(row, "CREATION_REDEMPTION_UNIT", "CreationRedemptionUnit")
        ),
        nav_per_cu=_to_float(_pick(row, "NAVPERCU", "NAVperCU")),
        nav_per_share=_to_float(_pick(row, "NAV")),
        creation_limit=_to_positive_int(_pick(row, "CREATION_LIMIT", "CreationLimit")),
        redemption_limit=_to_positive_int(_pick(row, "REDEMPTION_LIMIT", "RedemptionLimit")),
        net_creation_limit=_to_positive_int(
            _pick(row, "NET_CREATION_LIMIT", "NetCreationLimit")
        ),
        net_redemption_limit=_to_positive_int(
            _pick(row, "NET_REDEMPTION_LIMIT", "NetRedemptionLimit")
        ),
        account_creation_limit=_to_positive_int(
            _pick(
                row,
                "CREATION_LIMIT_PER_ACCT",
                "CreationLimitPerAcct",
                "CreationLimitPerUser",
            )
        ),
        account_redemption_limit=_to_positive_int(
            _pick(
                row,
                "REDEMPTION_LIMIT_PER_ACCT",
                "RedemptionLimitPerAcct",
                "RedemptionLimitPerUser",
            )
        ),
        account_net_creation_limit=_to_positive_int(
            _pick(
                row,
                "NET_CREATION_LIMIT_PER_ACCT",
                "NetCreationLimitPerAcct",
                "NetCreationLimitPerUser",
            )
        ),
        account_net_redemption_limit=_to_positive_int(
            _pick(
                row,
                "NET_REDEMPTION_LIMIT_PER_ACCT",
                "NetRedemptionLimitPerAcct",
                "NetRedemptionLimitPerUser",
            )
        ),
        creation_redemption_mode=(
            str(
                _pick(
                    row,
                    "CREATION_REDEMPTION_MECHANISM",
                    "CreationRedemptionMechanism",
                )
                or ""
            ).strip()
            or None
        ),
        source_url=source_url,
        fetched_at=fetched_at,
        raw_header=dict(row),
    )


def fetch_sse_pcf(code: str, *, timeout: int = 20) -> PcfSnapshot:
    payload = fetch_json(
        SSE_QUERY_API,
        {"isPagination": "false", "FUNDID2": code, "sqlId": SSE_PCF_BASIC_SQL},
        referer=SSE_PCF_PAGE,
        timeout=timeout,
    )
    rows = payload.get("result") or []
    if not rows:
        raise RuntimeError(f"SSE PCF basic row missing: {code}")
    return parse_sse_basic_row(
        code,
        rows[0],
        source_url=f"{SSE_PCF_PAGE}detail.shtml?fundid={code}",
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _flatten_xml_header(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in root.iter():
        if _local_name(node.tag) == "Component":
            continue
        if list(node):
            continue
        name = _local_name(node.tag)
        text = (node.text or "").strip()
        if text and name not in result:
            result[name] = text
    return result


def _parse_szse_bool(value: Any) -> bool | None:
    text = str(value or "").strip().upper()
    if text in {"Y", "1", "TRUE", "允许", "开放"}:
        return True
    if text in {"N", "0", "FALSE", "禁止", "不允许"}:
        return False
    return None


def parse_szse_xml(
    code: str,
    xml_text: str,
    *,
    source_url: str | None = None,
    fetched_at: str | None = None,
) -> PcfSnapshot:
    root = ET.fromstring(xml_text.encode("utf-8"))
    header = _flatten_xml_header(root)
    return PcfSnapshot(
        code=code,
        exchange="SZSE",
        trade_date=str(_pick(header, "TradingDay") or "") or None,
        creation_allowed=_parse_szse_bool(_pick(header, "Creation")),
        redemption_allowed=_parse_szse_bool(_pick(header, "Redemption")),
        creation_redemption_unit=_to_positive_int(_pick(header, "CreationRedemptionUnit")),
        nav_per_cu=_to_float(_pick(header, "NAVperCU")),
        nav_per_share=_to_float(_pick(header, "NAV")),
        creation_limit=_to_positive_int(_pick(header, "CreationLimit")),
        redemption_limit=_to_positive_int(_pick(header, "RedemptionLimit")),
        net_creation_limit=_to_positive_int(_pick(header, "NetCreationLimit")),
        net_redemption_limit=_to_positive_int(_pick(header, "NetRedemptionLimit")),
        account_creation_limit=_to_positive_int(_pick(header, "CreationLimitPerUser")),
        account_redemption_limit=_to_positive_int(_pick(header, "RedemptionLimitPerUser")),
        account_net_creation_limit=_to_positive_int(
            _pick(header, "NetCreationLimitPerUser")
        ),
        account_net_redemption_limit=_to_positive_int(
            _pick(header, "NetRedemptionLimitPerUser")
        ),
        creation_redemption_mode=str(_pick(header, "Type") or "").strip() or None,
        source_url=source_url,
        fetched_at=fetched_at,
        raw_header=header,
    )


def fetch_szse_pcf(
    code: str,
    *,
    trade_date: date | str | None = None,
    timeout: int = 20,
) -> PcfSnapshot:
    if trade_date is None:
        day = date.today().strftime("%Y%m%d")
    elif isinstance(trade_date, date):
        day = trade_date.strftime("%Y%m%d")
    else:
        day = str(trade_date).replace("-", "")
    url = SZSE_PCF_XML.format(code=code, day=day)
    text = fetch_text(url, referer=SZSE_PCF_PAGE, timeout=timeout)
    return parse_szse_xml(
        code,
        text,
        source_url=url,
        fetched_at=datetime.now(timezone.utc).isoformat(),
    )


def fetch_pcf(
    code: str,
    exchange: str,
    *,
    trade_date: date | str | None = None,
    timeout: int = 20,
) -> PcfSnapshot:
    if exchange == "SSE":
        return fetch_sse_pcf(code, timeout=timeout)
    if exchange == "SZSE":
        return fetch_szse_pcf(code, trade_date=trade_date, timeout=timeout)
    raise ValueError(f"unsupported exchange: {exchange}")
