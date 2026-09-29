from __future__ import annotations

from dataclasses import dataclass

from .classification import FundTypeRecord
from .mapping import ResolverMappingCandidate


@dataclass(frozen=True)
class ResolverClassDecision:
    fund_code: str
    resolver_class: str
    reason: str


_CROSS_BORDER_KEYWORDS = (
    "恒生",
    "香港",
    "港股",
    "沪港深",
    "港美",
)

_COMMODITY_KEYWORDS = (
    "商品",
    "原油",
    "石油",
    "黄金",
    "白银",
    "大宗",
)


def classify_resolver(
    *,
    fund_code: str,
    fund_name: str,
    fund_type: FundTypeRecord | None,
    mapping: ResolverMappingCandidate | None,
) -> ResolverClassDecision:
    raw_type = (fund_type.fund_type_raw if fund_type else None) or ""
    tracking = (mapping.tracking_target_name if mapping else None) or ""
    joined = f"{fund_name} {raw_type} {tracking}".upper()

    is_qdii = "QDII" in joined or "海外" in raw_type
    is_index = "指数" in raw_type or bool(tracking)
    is_commodity = any(word in joined for word in _COMMODITY_KEYWORDS)
    is_cross_border_non_qdii = (
        not is_qdii
        and any(word in joined for word in _CROSS_BORDER_KEYWORDS)
    )

    if is_commodity:
        return ResolverClassDecision(
            fund_code=fund_code,
            resolver_class="R5_SPECIAL",
            reason="commodity_or_special_underlying",
        )

    if is_cross_border_non_qdii:
        return ResolverClassDecision(
            fund_code=fund_code,
            resolver_class="R5_SPECIAL",
            reason="cross_border_non_qdii",
        )

    if is_qdii and is_index:
        return ResolverClassDecision(
            fund_code=fund_code,
            resolver_class="R3_QDII_INDEX",
            reason="qdii_index",
        )

    if is_qdii:
        return ResolverClassDecision(
            fund_code=fund_code,
            resolver_class="R4_QDII_OTHER",
            reason="qdii_non_index",
        )

    if is_index:
        return ResolverClassDecision(
            fund_code=fund_code,
            resolver_class="R1_DOMESTIC_INDEX",
            reason="domestic_index",
        )

    return ResolverClassDecision(
        fund_code=fund_code,
        resolver_class="R2_DOMESTIC_OTHER",
        reason="domestic_non_index",
    )
