from __future__ import annotations

import re
from typing import Any


_CROSS_BORDER_WORDS = (
    "纳斯达克", "纳指", "标普", "道琼斯", "美国", "日经", "日本", "东证",
    "德国", "法国", "英国", "沙特", "印度", "韩国", "越南", "新加坡",
    "海外", "全球", "恒生", "香港", "港股", "中概", "亚洲", "东南亚",
)
_MIXED_WORDS = ("沪港深", "沪深港", "港股通")
_BOND_WORDS = ("债", "国开", "国债", "政金", "信用", "可转债", "城投", "同业存单")
_MONEY_WORDS = ("货币", "现金", "添益", "理财金")
_COMMODITY_WORDS = ("黄金", "金ETF", "上海金", "有色金属", "商品", "豆粕", "能源化工", "原油")
_ACTIVE_WORDS = ("主动", "增强策略", "精选")


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
    """Conservative, multi-dimensional ETF classification."""
    text = f"{name} {tracking_index or ''}".strip()
    raw = str(raw_exchange_class or "")
    pcf = str(pcf_type or "")

    if pcf == "4" or raw == "05" or _contains(text, _MONEY_WORDS):
        asset_class = "MONEY_MARKET"
    elif pcf in {"6", "7"} or raw in {"02", "32", "37"} or _contains(text, _BOND_WORDS):
        asset_class = "BOND"
    elif pcf == "5" or raw == "06" or _contains(text, _COMMODITY_WORDS):
        asset_class = "COMMODITY"
    elif raw or re.search(r"ETF", text, flags=re.I):
        asset_class = "EQUITY"
    else:
        asset_class = "OTHER"

    if _contains(text, _MIXED_WORDS):
        region_scope = "MIXED"
        qdii_flag: bool | None = None
        region_reason = "name/index contains mainland-HK mixed-market marker"
    elif _contains(text, _CROSS_BORDER_WORDS) or raw in {"04", "33"} or pcf == "2":
        region_scope = "CROSS_BORDER"
        qdii_flag = True if not _contains(text, ("港股通", "沪港深", "沪深港")) else None
        region_reason = "cross-border name/index or exchange PCF class"
    elif exchange in {"SSE", "SZSE"}:
        region_scope = "DOMESTIC"
        qdii_flag = False
        region_reason = "no cross-border marker in official metadata"
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
