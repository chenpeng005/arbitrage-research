from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .commodity_proxy_registry import CommodityProxyEntry
from .csi_component_proxy import CsiComponentWeightSet
from .index_proxy import IndexProxyMapping
from .mapping import ResolverMappingCandidate
from .nav import OfficialNavRecord
from .qdii_proxy_registry import QdiiProxyEntry
from .r1_pipeline import resolve_r1_batch
from .r3_pipeline import resolve_r3_one
from .r5_pipeline import resolve_r5_commodity_one
from .resolver import EstimatedNavResult
from .resolver_classification import ResolverClassDecision


@dataclass(frozen=True)
class EstimatedNavContext:
    resolver_classes: dict[str, ResolverClassDecision]
    mapping_candidates: dict[str, ResolverMappingCandidate]
    r1_proxy_mappings: dict[str, IndexProxyMapping]
    qdii_proxy_registry: dict[str, QdiiProxyEntry]
    previous_trading_day: date
    commodity_proxy_registry: dict[str, CommodityProxyEntry] = field(
        default_factory=dict
    )
    r1_component_weight_sets: dict[str, CsiComponentWeightSet] = field(
        default_factory=dict
    )


def _unavailable(
    *,
    code: str,
    resolver_class: str,
    error: str,
) -> EstimatedNavResult:
    return EstimatedNavResult(
        fund_code=code,
        estimated_nav=None,
        estimated_nav_time=None,
        estimated_nav_status="UNAVAILABLE",
        estimated_nav_quality="UNKNOWN",
        resolver_class=resolver_class,
        resolver_method="UNAVAILABLE",
        proxy_id="",
        proxy_time=None,
        proxy_return=None,
        fx_return=None,
        exposure_ratio_used=None,
        tracking_adjustment_used=None,
        error=error,
    )


def resolve_estimated_nav_lane(
    *,
    official_navs: list[OfficialNavRecord],
    context: EstimatedNavContext,
    as_of: datetime,
    timeout: int = 10,
) -> list[EstimatedNavResult]:
    nav_map = {row.code: row for row in official_navs}
    results: dict[str, EstimatedNavResult] = {}

    r1_codes = [
        code
        for code, decision in context.resolver_classes.items()
        if decision.resolver_class == "R1_DOMESTIC_INDEX"
        and code in nav_map
    ]
    if r1_codes:
        r1_results = resolve_r1_batch(
            fund_codes=r1_codes,
            official_navs=nav_map,
            mapping_candidates=context.mapping_candidates,
            proxy_mappings=context.r1_proxy_mappings,
            component_weight_sets=context.r1_component_weight_sets,
            expected_anchor_date=context.previous_trading_day,
            as_of=as_of,
            timeout=timeout,
        )
        for row in r1_results:
            results[row.fund_code] = row

    for code, decision in context.resolver_classes.items():
        if code not in nav_map or code in results:
            continue

        if decision.resolver_class == "R3_QDII_INDEX":
            mapping = context.mapping_candidates.get(code)
            proxy = context.qdii_proxy_registry.get(code)
            if mapping is None:
                results[code] = _unavailable(
                    code=code,
                    resolver_class=decision.resolver_class,
                    error="MISSING_MAPPING_CANDIDATE",
                )
                continue
            if proxy is None:
                results[code] = _unavailable(
                    code=code,
                    resolver_class=decision.resolver_class,
                    error="MISSING_QDII_PROXY_REGISTRY",
                )
                continue
            results[code] = resolve_r3_one(
                nav=nav_map[code],
                mapping=mapping,
                proxy=proxy,
                as_of=as_of,
                timeout=timeout,
            )
            continue

        if decision.resolver_class == "R5_SPECIAL":
            proxy = context.commodity_proxy_registry.get(code)
            if proxy is None:
                results[code] = _unavailable(
                    code=code,
                    resolver_class=decision.resolver_class,
                    error="MISSING_R5_PROXY_REGISTRY",
                )
                continue
            results[code] = resolve_r5_commodity_one(
                nav=nav_map[code],
                proxy=proxy,
                as_of=as_of,
                timeout=timeout,
            )
            continue

        results[code] = _unavailable(
            code=code,
            resolver_class=decision.resolver_class,
            error="RESOLVER_CLASS_NOT_IMPLEMENTED",
        )

    # Preserve deterministic ordering and explicit records for known NAV rows.
    for code in nav_map:
        if code not in results:
            decision = context.resolver_classes.get(code)
            results[code] = _unavailable(
                code=code,
                resolver_class=(
                    decision.resolver_class
                    if decision is not None
                    else "UNKNOWN"
                ),
                error="NO_RESOLVER_CLASS",
            )

    return [results[code] for code in sorted(results)]