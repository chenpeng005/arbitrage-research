from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .classification import FundTypeRecord
from .commodity_proxy_registry import (
    CommodityProxyEntry,
    load_commodity_proxy_registry,
)
from .estimated_nav_lane import EstimatedNavContext
from .index_proxy import (
    IndexProxyMapping,
    fetch_index_proxy_mapping,
    proxy_from_tracking_index_code,
)
from .mapping import ResolverMappingCandidate
from .qdii_proxy_registry import QdiiProxyEntry, load_qdii_proxy_registry
from .resolver_classification import ResolverClassDecision, classify_resolver
from .tracking_index import TrackingIndexRecord, fetch_active_tracking_index_map
from .universe import LofIdentity


MODULE_DIR = Path(__file__).resolve().parent
DEFAULT_QDII_PROXY_REGISTRY = (
    MODULE_DIR / "data" / "qdii_index_proxy_registry_v0_1.json"
)
DEFAULT_COMMODITY_PROXY_REGISTRY = (
    MODULE_DIR / "data" / "commodity_proxy_registry_v0_1.json"
)


@dataclass(frozen=True)
class EstimatedNavContextBuildResult:
    context: EstimatedNavContext
    tracking_index_map: dict[str, TrackingIndexRecord]
    r1_resolved_count: int
    r1_unresolved_count: int


def _mapping_from_tracking(
    row: TrackingIndexRecord,
) -> ResolverMappingCandidate:
    return ResolverMappingCandidate(
        fund_code=row.fund_code,
        tracking_target_name=row.tracking_index_name,
        benchmark_text=None,
        exposure_ratio_candidate=None,
        source=row.source,
        error=row.error,
    )


def _merge_mapping(
    *,
    direct: ResolverMappingCandidate | None,
    f10: ResolverMappingCandidate | None,
) -> ResolverMappingCandidate | None:
    if direct is None:
        return f10
    if f10 is None:
        return direct

    return ResolverMappingCandidate(
        fund_code=direct.fund_code,
        tracking_target_name=(
            f10.tracking_target_name
            or direct.tracking_target_name
        ),
        benchmark_text=f10.benchmark_text,
        exposure_ratio_candidate=f10.exposure_ratio_candidate,
        source=f"{direct.source}+{f10.source}",
        error=None if direct.error is None or f10.error is None else direct.error,
    )


def build_estimated_nav_context(
    *,
    universe: list[LofIdentity],
    type_records: dict[tuple[str, str], FundTypeRecord],
    previous_trading_day: date,
    f10_mapping_candidates: dict[str, ResolverMappingCandidate] | None = None,
    qdii_proxy_registry: dict[str, QdiiProxyEntry] | None = None,
    commodity_proxy_registry: dict[str, CommodityProxyEntry] | None = None,
    tracking_index_override: dict[str, TrackingIndexRecord] | None = None,
    timeout: int = 15,
    allow_name_search_fallback: bool = False,
) -> EstimatedNavContextBuildResult:
    """Build the low-frequency context consumed by the high-frequency NAV lane.

    Direct fund->tracking-index codes are the main mapping source.
    F10 data only enriches benchmark/exposure when available.
    """
    f10_mapping_candidates = f10_mapping_candidates or {}

    tracking_map = (
        dict(tracking_index_override)
        if tracking_index_override is not None
        else fetch_active_tracking_index_map(
            universe,
            timeout=timeout,
        )
    )

    mapping_candidates: dict[str, ResolverMappingCandidate] = {}
    for identity in universe:
        direct_row = tracking_map.get(identity.code)
        direct = (
            _mapping_from_tracking(direct_row)
            if direct_row is not None
            else None
        )
        merged = _merge_mapping(
            direct=direct,
            f10=f10_mapping_candidates.get(identity.code),
        )
        if merged is not None:
            mapping_candidates[identity.code] = merged

    resolver_classes: dict[str, ResolverClassDecision] = {}
    for identity in universe:
        resolver_classes[identity.code] = classify_resolver(
            fund_code=identity.code,
            fund_name=identity.name,
            fund_type=type_records.get((identity.exchange, identity.code)),
            mapping=mapping_candidates.get(identity.code),
        )

    r1_proxy_mappings: dict[str, IndexProxyMapping] = {}
    for identity in universe:
        decision = resolver_classes[identity.code]
        if decision.resolver_class != "R1_DOMESTIC_INDEX":
            continue

        tracking = tracking_map.get(identity.code)
        if (
            tracking is None
            or not tracking.tracking_index_code
        ):
            continue

        proxy = proxy_from_tracking_index_code(
            tracking_target_name=(
                tracking.tracking_index_name
                or identity.name
            ),
            index_code=tracking.tracking_index_code,
            index_name=tracking.tracking_index_name,
        )

        if (
            proxy.status != "RESOLVED"
            and allow_name_search_fallback
            and tracking.tracking_index_name
        ):
            proxy = fetch_index_proxy_mapping(
                tracking.tracking_index_name,
                timeout=timeout,
            )

        r1_proxy_mappings[identity.code] = proxy

    qdii_registry = (
        qdii_proxy_registry
        if qdii_proxy_registry is not None
        else load_qdii_proxy_registry(DEFAULT_QDII_PROXY_REGISTRY)
    )
    commodity_registry = (
        commodity_proxy_registry
        if commodity_proxy_registry is not None
        else load_commodity_proxy_registry(DEFAULT_COMMODITY_PROXY_REGISTRY)
    )

    r1_codes = [
        code
        for code, decision in resolver_classes.items()
        if decision.resolver_class == "R1_DOMESTIC_INDEX"
    ]
    resolved = sum(
        1
        for code in r1_codes
        if (
            code in r1_proxy_mappings
            and r1_proxy_mappings[code].status == "RESOLVED"
        )
    )

    context = EstimatedNavContext(
        resolver_classes=resolver_classes,
        mapping_candidates=mapping_candidates,
        r1_proxy_mappings=r1_proxy_mappings,
        qdii_proxy_registry=qdii_registry,
        commodity_proxy_registry=commodity_registry,
        previous_trading_day=previous_trading_day,
    )

    return EstimatedNavContextBuildResult(
        context=context,
        tracking_index_map=tracking_map,
        r1_resolved_count=resolved,
        r1_unresolved_count=len(r1_codes) - resolved,
    )
