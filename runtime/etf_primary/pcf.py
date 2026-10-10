from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any, Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from .http import fetch_json, fetch_text
from .models import PcfSnapshot


SSE_QUERY_API = "https://query.sse.com.cn/commonQuery.do"
SSE_PCF_PAGE = "https://www.sse.com.cn/disclosure/fund/etflist/"
SSE_PCF_BASIC_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C"

SZSE_PCF_PAGE = "https://www.szse.cn/disclosure/fund/currency/index.html"
SZSE_PCF_LIST_API = "https://www.szse.cn/api/report/ShowReport/data"
SZSE_PCF_LIST_CATALOG = "sgshqd"
SZSE_REPORTDOCS_BASE = "https://reportdocs.static.szse.cn"
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
        # PCFs commonly encode an unset/unlimited cap as blank, dash, or zero.
        # Keep that distinct from a confirmed zero-capacity state; the creation
        # switch carries the explicit open/closed fact.
        return None
    return int(number)


def _find_present(mapping: dict[str, Any], *keys: str) -> tuple[bool, Any]:
    for key in keys:
        if key in mapping:
            return True, mapping[key]
    lower = {str(k).lower(): v for k, v in mapping.items()}
    for key in keys:
        if key.lower() in lower:
            return True, lower[key.lower()]
    return False, None


def _normalize_ymd(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = re.sub(r"[^0-9]", "", text)
    return digits if re.fullmatch(r"\d{8}", digits) else None


def _is_unlimited_cap_value(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip().replace(",", "").replace("￥", "").replace("元", "")
    if text in {"", "-", "--", "无", "不限", "不设上限", "None", "null", "NULL"}:
        return True
    try:
        return float(text) <= 0
    except ValueError:
        return False


def _capacity_status_from_raw(
    mapping: dict[str, Any],
    key_groups: tuple[tuple[str, ...], ...],
    creation_allowed: bool | None,
) -> str:
    if creation_allowed is False:
        return "CLOSED"
    seen = False
    all_unlimited_markers = True
    for keys in key_groups:
        present, value = _find_present(mapping, *keys)
        if not present:
            continue
        seen = True
        if _to_positive_int(value) is not None:
            return "LIMITED" if creation_allowed is True else "UNKNOWN"
        if not _is_unlimited_cap_value(value):
            all_unlimited_markers = False
    if creation_allowed is True and seen and all_unlimited_markers:
        return "UNLIMITED"
    return "UNKNOWN"


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
        nav_date=_normalize_ymd(_pick(row, "PRE_TRADING_DAY", "PreTradingDay")),
        market_capacity_status=_capacity_status_from_raw(
            row,
            (("CREATION_LIMIT", "CreationLimit"), ("NET_CREATION_LIMIT", "NetCreationLimit")),
            creation_allowed,
        ),
        account_capacity_status=_capacity_status_from_raw(
            row,
            (
                ("CREATION_LIMIT_PER_ACCT", "CreationLimitPerAcct", "CreationLimitPerUser"),
                ("NET_CREATION_LIMIT_PER_ACCT", "NetCreationLimitPerAcct", "NetCreationLimitPerUser"),
            ),
            creation_allowed,
        ),
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


def fetch_sse_pcf_bulk(
    *,
    codes: Iterable[str] | None = None,
    timeout: int = 30,
) -> dict[str, PcfSnapshot]:
    """Fetch the SSE PCF header table in one official query.

    The same official SQL endpoint used by a single-fund query returns the full
    PCF header table when FUNDID2 is omitted. The table includes historical or
    delisted rows, so callers should pass the current official universe codes.
    """
    wanted = {str(code) for code in codes} if codes is not None else None
    payload = fetch_json(
        SSE_QUERY_API,
        {"isPagination": "false", "sqlId": SSE_PCF_BASIC_SQL},
        referer=SSE_PCF_PAGE,
        timeout=timeout,
    )
    rows = payload.get("result") or []
    fetched_at = datetime.now(timezone.utc).isoformat()
    result: dict[str, PcfSnapshot] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        code = str(
            _pick(row, "TRADE_CODE", "FundInstrumentID", "FUND_CODE", "FUNDID2")
            or ""
        ).strip()
        if not code or (wanted is not None and code not in wanted):
            continue
        snapshot = parse_sse_basic_row(
            code,
            row,
            source_url=f"{SSE_PCF_PAGE}detail.shtml?fundid={code}",
            fetched_at=fetched_at,
        )
        previous = result.get(code)
        if previous is None or str(snapshot.trade_date or "") >= str(previous.trade_date or ""):
            result[code] = snapshot
    return result


def latest_pcf_trade_date(snapshots: Iterable[PcfSnapshot]) -> str | None:
    dates = [str(item.trade_date) for item in snapshots if item.trade_date and re.fullmatch(r"\d{8}", str(item.trade_date))]
    return max(dates) if dates else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _flatten_xml_header(root: ET.Element) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in root.iter():
        if _local_name(node.tag) in {"Component", "Components", "ComponentList"}:
            continue
        if list(node):
            continue
        name = _local_name(node.tag)
        text = (node.text or "").strip()
        if name not in result:
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
        nav_date=_normalize_ymd(_pick(header, "PreTradingDay")),
        market_capacity_status=_capacity_status_from_raw(
            header,
            (("CreationLimit",), ("NetCreationLimit",)),
            _parse_szse_bool(_pick(header, "Creation")),
        ),
        account_capacity_status=_capacity_status_from_raw(
            header,
            (("CreationLimitPerUser",), ("NetCreationLimitPerUser",)),
            _parse_szse_bool(_pick(header, "Creation")),
        ),
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


class _AnchorCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self._current = {"attrs": dict(attrs), "text": []}
        self.anchors.append(self._current)

    def handle_data(self, data: str) -> None:
        if self._current is not None:
            self._current["text"].append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self._current = None


@dataclass(frozen=True)
class SzsePcfListEntry:
    code: str
    trade_date: str
    page_label: str
    source_page_url: str
    xml_candidate_urls: tuple[str, ...]


def _szse_xml_candidates(code: str, ymd: str) -> tuple[str, ...]:
    return (
        f"{SZSE_REPORTDOCS_BASE}/files/text/ETFDown/pcf_{code}_{ymd}.xml",
        f"{SZSE_REPORTDOCS_BASE}/files/text/ETFDown/{code}ETF{ymd}.xml",
    )


def parse_szse_pcf_list_item(html_text: str, expected_day: str) -> SzsePcfListEntry:
    expected_ymd = expected_day.replace("-", "")
    parser = _AnchorCollector()
    parser.feed(html_text)
    if not parser.anchors:
        raise ValueError("SZSE PCF list item has no anchor")

    first = parser.anchors[0]
    attrs = first.get("attrs") or {}
    title = "".join(first.get("text") or []).strip()
    page_label = re.sub(r"\(\d{4}-\d{2}-\d{2}\)$", "", title).strip()
    opencode_path = str(attrs.get("encode-open") or "").strip()
    opencode_name = Path(opencode_path).name
    match = re.search(r"ETF(?P<code>\d{6})(?P<ymd>\d{8})\.txt$", opencode_name, re.I)
    if not match:
        raise ValueError("cannot extract SZSE ETF code/date from official PCF list item")
    code = match.group("code")
    ymd = match.group("ymd")
    if ymd != expected_ymd:
        raise ValueError(f"SZSE PCF list date mismatch: {ymd} != {expected_ymd}")

    candidates: list[str] = []
    source_page_url = SZSE_PCF_PAGE
    if len(parser.anchors) > 1:
        download_attrs = parser.anchors[1].get("attrs") or {}
        href = str(download_attrs.get("href") or "").strip()
        if href:
            source_page_url = urljoin("https://www.szse.cn", href)
            parsed = urlparse(source_page_url)
            query = parse_qs(parsed.query)
            base_path = str((query.get("path") or [""])[0] or "")
            if base_path and not base_path.endswith("/"):
                base_path += "/"
            filenames = str((query.get("filename") or [""])[0] or "")
            for name in [item for item in filenames.split(";") if item]:
                candidates.append(urljoin(SZSE_REPORTDOCS_BASE, f"{base_path}{name}.xml"))

    for url in _szse_xml_candidates(code, ymd):
        if url not in candidates:
            candidates.append(url)
    return SzsePcfListEntry(
        code=code,
        trade_date=ymd,
        page_label=page_label,
        source_page_url=source_page_url,
        xml_candidate_urls=tuple(candidates),
    )


def fetch_szse_pcf_day_index(
    trade_date: date | str,
    *,
    timeout: int = 30,
) -> dict[str, SzsePcfListEntry]:
    if isinstance(trade_date, date):
        iso_day = trade_date.isoformat()
    else:
        raw = str(trade_date)
        iso_day = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}" if re.fullmatch(r"\d{8}", raw) else raw

    params = {
        "SHOWTYPE": "JSON",
        "CATALOGID": SZSE_PCF_LIST_CATALOG,
        "TABKEY": "tab1",
        "txtStart": iso_day,
        "txtEnd": iso_day,
        "PAGENO": 1,
    }
    first = fetch_json(
        SZSE_PCF_LIST_API,
        params,
        referer=SZSE_PCF_PAGE,
        timeout=timeout,
    )
    if not isinstance(first, list) or not first:
        return {}
    metadata = first[0].get("metadata") or {}
    page_count = int(metadata.get("pagecount") or 1)
    result: dict[str, SzsePcfListEntry] = {}

    for page_no in range(1, page_count + 1):
        payload = first if page_no == 1 else fetch_json(
            SZSE_PCF_LIST_API,
            {**params, "PAGENO": page_no},
            referer=SZSE_PCF_PAGE,
            timeout=timeout,
        )
        for block in payload or []:
            for row in (block or {}).get("data") or []:
                html_text = str((row or {}).get("jjdm") or "")
                if not html_text:
                    continue
                entry = parse_szse_pcf_list_item(html_text, iso_day)
                result[entry.code] = entry
    return result


def _fetch_szse_entry(entry: SzsePcfListEntry, *, timeout: int) -> PcfSnapshot:
    last_error: Exception | None = None
    for url in entry.xml_candidate_urls:
        try:
            text = fetch_text(
                url,
                referer=SZSE_PCF_PAGE,
                timeout=timeout,
                attempts=2,
                base_delay_seconds=0.2,
            )
            stripped = text.lstrip()
            if not stripped.startswith("<") or "html" in stripped[:120].lower():
                continue
            snapshot = parse_szse_xml(
                entry.code,
                text,
                source_url=url,
                fetched_at=datetime.now(timezone.utc).isoformat(),
            )
            if snapshot.trade_date and snapshot.trade_date != entry.trade_date:
                raise ValueError(
                    f"SZSE PCF trading day mismatch for {entry.code}: "
                    f"{snapshot.trade_date} != {entry.trade_date}"
                )
            return snapshot
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"SZSE PCF fetch failed for {entry.code}: {last_error}")


def fetch_szse_pcf_bulk(
    *,
    codes: Iterable[str],
    trade_date: date | str,
    timeout: int = 20,
    max_workers: int = 6,
) -> tuple[dict[str, PcfSnapshot], dict[str, str]]:
    wanted = {str(code) for code in codes}
    index = fetch_szse_pcf_day_index(trade_date, timeout=max(timeout, 30))
    snapshots: dict[str, PcfSnapshot] = {}
    errors: dict[str, str] = {}

    available = {code: item for code, item in index.items() if code in wanted}
    for code in sorted(wanted - set(available)):
        errors[code] = "not_in_official_day_index"

    workers = max(1, min(int(max_workers), 12))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_szse_entry, entry, timeout=timeout): code
            for code, entry in available.items()
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                snapshots[code] = future.result()
            except Exception as exc:
                errors[code] = f"{type(exc).__name__}: {exc}"

    return snapshots, errors


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
