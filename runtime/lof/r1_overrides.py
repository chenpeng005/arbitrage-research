from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .index_proxy import IndexProxyMapping
from .mapping import ResolverMappingCandidate
from .tracking_index import TrackingIndexRecord


@dataclass(frozen=True)
class R1TargetEtfOverride:
    fund_code: str
    etf_code: str
    etf_name: str
    exposure_ratio: Decimal
    evidence: str


# Explicit only. Never infer share-class relationships from names.
TRACKING_INDEX_SHARE_ALIASES: dict[str, str] = {
    "501006": "501005",
}


TARGET_ETF_OVERRIDES: dict[str, R1TargetEtfOverride] = {
    "501029": R1TargetEtfOverride(
        fund_code="501029",
        etf_code="562060",
        etf_name="标普A股红利ETF华宝",
        exposure_ratio=Decimal("0.95"),
        evidence="HUABAO_TARGET_ETF_562060",
    ),
}


def apply_tracking_index_share_aliases(
    tracking_map: dict[str, TrackingIndexRecord],
    *,
    active_codes: set[str],
) -> dict[str, TrackingIndexRecord]:
    result = dict(tracking_map)
    for target_code, source_code in TRACKING_INDEX_SHARE_ALIASES.items():
        if target_code not in active_codes:
            continue

        direct = result.get(target_code)
        if direct is not None and direct.available:
            continue

        source = result.get(source_code)
        if source is None or not source.available:
            continue

        result[target_code] = TrackingIndexRecord(
            fund_code=target_code,
            fund_name=direct.fund_name if direct is not None else None,
            tracking_index_code=source.tracking_index_code,
            tracking_index_name=source.tracking_index_name,
            market_bucket=source.market_bucket,
            source=f"R1_AUDITED_SHARE_ALIAS:{source_code}:{source.source}",
            fetched_at=source.fetched_at,
            error=None,
        )
    return result


def target_etf_proxy_override(
    fund_code: str,
) -> IndexProxyMapping | None:
    override = TARGET_ETF_OVERRIDES.get(fund_code)
    if override is None:
        return None

    return IndexProxyMapping(
        tracking_target_name=override.etf_name,
        index_code=override.etf_code,
        index_name=override.etf_name,
        quote_id=f"1.{override.etf_code}",
        market_num="1",
        tencent_symbol=f"sh{override.etf_code}",
        xueqiu_symbol=f"SH{override.etf_code}",
        source="R1_TARGET_ETF_OVERRIDE",
        status="RESOLVED",
        error=None,
    )


def apply_target_etf_mapping_override(
    fund_code: str,
    mapping: ResolverMappingCandidate | None,
) -> ResolverMappingCandidate | None:
    override = TARGET_ETF_OVERRIDES.get(fund_code)
    if override is None:
        return mapping

    return ResolverMappingCandidate(
        fund_code=fund_code,
        tracking_target_name=override.etf_name,
        benchmark_text=(
            mapping.benchmark_text
            if mapping is not None
            else None
        ),
        exposure_ratio_candidate=override.exposure_ratio,
        source=(
            f"{mapping.source}+R1_TARGET_ETF_OVERRIDE"
            if mapping is not None
            else "R1_TARGET_ETF_OVERRIDE"
        ),
        error=None,
    )
