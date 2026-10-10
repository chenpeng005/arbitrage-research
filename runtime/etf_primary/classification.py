from __future__ import annotations

import re
from typing import Any


_CROSS_BORDER_WORDS = (
    "纳斯达克", "纳指", "道琼斯", "美国", "日经", "日本", "东证",
    "德国", "法国", "英国", "沙特", "印度", "韩国", "越南", "新加坡",
    "海外", "全球", "香港", "港股", "中概", "亚洲", "东南亚",
)
_MIXED_WORDS = ("沪港深", "沪深港")
_CONNECT_WORDS = ("港股通",)
_DOMESTIC_SCOPE_WORDS = ("A股", "中国A股", "上证", "深证", "沪深", "创业板", "科创板", "北证")
_PROVIDER_CROSS_BORDER_WORDS = ("恒生", "标普")

# SSE fund subclasses are official product categories and must outrank index-provider
# names. In particular, subclass 03 is a domestic equity ETF class even when an
# index is branded by S&P or Hang Seng.
_SSE_MIXED_CLASSES = {"08"}
_SSE_CROSS_BORDER_CLASSES = {"04", "33"}
_SSE_DOMESTIC_CLASSES = {"01", "02", "03", "05", "06", "09", "31", "32", "37"}
_BOND_WORDS = ("债", "国开", "国债", "政金", "信用", "可转债", "城投", "同业存单")
# Generic "现金" is intentionally excluded: many equity ETFs track free-cash-flow indices.
_MONEY_WORDS = ("货币", "添益", "理财金", "快线", "快钱")
# Do not classify equity-sector ETFs such as "有色金属ETF" as commodity ETFs.
# Exchange PCF/raw class is authoritative when available; keywords are only a fallback.
_COMMODITY_WORDS = ("黄金ETF", "黄金基金", "上海金", "商品期货", "豆粕ETF", "原油ETF")
_ACTIVE_WORDS = ("主动ETF", "主动管理ETF")


def _contains(text: str, words: tuple[str, ...]) -> bool:
    return any(word in text for word in words)


def classify_etf(
    *,
    name: str,
    tracking_index: str | None = None,
    exchange: str,
    raw_exchange_class: str | None = None,
    pcf_type: str | None = None,
) -> dict[str, Any]:
    """Conservative, multi-dimensional ETF classification.

    Region and asset dimensions are intentionally orthogonal. In particular,
    a pure 港股通 ETF is cross-border exposure but is not automatically QDII;
    only 沪港深 / 沪深港 products are treated as mixed mainland-HK exposure.
    """
    text = f"{name} {tracking_index or ''}".strip()
    raw = str(raw_exchange_class or "")
    raw_classes = {item for item in raw.split(",") if item}
    pcf = str(pcf_type or "")

    if pcf == "4" or "05" in raw_classes or _contains(text, _MONEY_WORDS):
        asset_class = "MONEY_MARKET"
    elif pcf in {"6", "7"} or raw_classes & {"02", "32", "37"} or _contains(text, _BOND_WORDS):
        asset_class = "BOND"
    elif pcf == "5" or "06" in raw_classes or _contains(text, _COMMODITY_WORDS):
        asset_class = "COMMODITY"
    elif raw_classes or re.search(r"ETF", text, flags=re.I):
        asset_class = "EQUITY"
    else:
        asset_class = "OTHER"

    # Region/QDII classification is evidence-ranked: official exchange class first,
    # then an explicit PCF type where the exchange supplies one, and only then
    # textual investment-scope markers. Provider brands alone never override an
    # official domestic class.
    if exchange == "SSE" and raw_classes & _SSE_MIXED_CLASSES:
        region_scope = "MIXED"
        qdii_flag: bool | None = None
        region_reason = "official SSE mixed-market class"
    elif exchange == "SSE" and raw_classes & _SSE_CROSS_BORDER_CLASSES:
        region_scope = "CROSS_BORDER"
        if _contains(text, _CONNECT_WORDS):
            qdii_flag = False
            region_reason = "official SSE cross-border class with Stock Connect scope"
        else:
            qdii_flag = True
            region_reason = "official SSE cross-border class"
    elif exchange == "SSE" and raw_classes & _SSE_DOMESTIC_CLASSES:
        region_scope = "DOMESTIC"
        qdii_flag = False
        region_reason = "official SSE domestic class"
    elif pcf == "2":
        region_scope = "CROSS_BORDER"
        qdii_flag = True
        region_reason = "official PCF cross-border type"
    elif _contains(text, _MIXED_WORDS):
        region_scope = "MIXED"
        qdii_flag = None
        region_reason = "mainland-HK mixed investment scope"
    elif _contains(text, _CONNECT_WORDS):
        region_scope = "CROSS_BORDER"
        qdii_flag = False
        region_reason = "Hong Kong exposure through Stock Connect marker"
    elif _contains(text, _DOMESTIC_SCOPE_WORDS):
        region_scope = "DOMESTIC"
        qdii_flag = False
        region_reason = "explicit mainland/A-share investment scope"
    elif _contains(text, _CROSS_BORDER_WORDS) or _contains(text, _PROVIDER_CROSS_BORDER_WORDS):
        region_scope = "CROSS_BORDER"
        qdii_flag = True
        region_reason = "explicit overseas/HK investment scope fallback"
    elif exchange in {"SSE", "SZSE"}:
        region_scope = "DOMESTIC"
        qdii_flag = False
        region_reason = "no cross-border evidence in official metadata or investment scope"
    else:
        region_scope = "UNKNOWN"
        qdii_flag = None
        region_reason = "insufficient metadata"

    if _contains(text, _ACTIVE_WORDS):
        strategy_style = "ACTIVE"
    elif tracking_index:
        strategy_style = "INDEX"
    else:
        strategy_style = "UNKNOWN"

    reason = (
        f"asset={asset_class}; region={region_reason}; "
        f"raw_class={raw or '-'}; pcf_type={pcf or '-'}"
    )
    return {
        "region_scope": region_scope,
        "asset_class": asset_class,
        "strategy_style": strategy_style,
        "qdii_flag": qdii_flag,
        "classification_reason": reason,
    }
