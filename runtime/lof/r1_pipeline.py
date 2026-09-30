from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

from .csi_component_proxy import (
    CsiComponentWeightSet,
    reconstruct_csi_component_quotes,
)
from .domestic_index_resolver import resolve_r1_from_previous_close
from .index_proxy import IndexProxyMapping
from .index_quote import IndexQuote
from .index_quote_xueqiu import fetch_index_quotes_for_mappings
from .mapping import ResolverMappingCandidate
from .nav import OfficialNavRecord
from .resolver import EstimatedNavResult


def _unavailable(
    *,
    fund_code: str,
    proxy_id: str | None,
    error: str,
) -> EstimatedNavResult:
    return EstimatedNavResult(
        fund_code=fund_code,
        estimated_nav=None,
        estimated_nav_time=None,
        estimated_nav_status="UNAVAILABLE",
        estimated_nav_quality="UNKNOWN",
        resolver_class="R1_DOMESTIC_INDEX",
        resolver_method="INDEX_PROXY_PREV_CLOSE",
        proxy_id=proxy_id or "",
        proxy_time=None,
        proxy_return=None,
        fx_return=None,
        exposure_ratio_used=None,
        tracking_adjustment_used=None,
        error=error,
    )


def resolve_r1_batch(
    *,
    fund_codes: Iterable[str],
    official_navs: dict[str, OfficialNavRecord],
    mapping_candidates: dict[str, ResolverMappingCandidate],
    proxy_mappings: dict[str, IndexProxyMapping],
    component_weight_sets: dict[str, CsiComponentWeightSet] | None = None,
    expected_anchor_date: date,
    as_of: datetime,
    max_proxy_age_seconds: int = 60,
    timeout: int = 10,
) -> list[EstimatedNavResult]:
    """Resolve R1 funds using shared index quotes.

    Low-frequency fund->tracking-target and tracking-target->proxy mappings are
    supplied by the caller. This function only performs the high-frequency
    quote+valuation step.
    """
    codes = sorted({str(code).strip() for code in fund_codes if str(code).strip()})

    # Fetch every unique proxy in batches. Tencent is primary; only missing
    # or unsupported proxies fall back to a batched Xueqiu request.
    unique_proxy_map: dict[
        tuple[str | None, str | None],
        IndexProxyMapping,
    ] = {}
    for code in codes:
        proxy = proxy_mappings.get(code)
        if proxy is None or proxy.status != "RESOLVED":
            continue
        unique_proxy_map[(proxy.tencent_symbol, proxy.xueqiu_symbol)] = proxy

    component_weight_sets = component_weight_sets or {}
    component_proxy_map = {
        key: proxy
        for key, proxy in unique_proxy_map.items()
        if (
            proxy.index_code
            and proxy.index_code in component_weight_sets
            and component_weight_sets[proxy.index_code].available
        )
    }
    standard_proxy_map = {
        key: proxy
        for key, proxy in unique_proxy_map.items()
        if key not in component_proxy_map
    }

    quote_cache = fetch_index_quotes_for_mappings(
        standard_proxy_map.values(),
        timeout=timeout,
    )

    component_quotes = reconstruct_csi_component_quotes(
        {
            proxy.index_code: component_weight_sets[proxy.index_code]
            for proxy in component_proxy_map.values()
            if proxy.index_code
        },
        as_of=as_of,
        timeout=timeout,
    )
    component_failures: list[IndexProxyMapping] = []
    for key, proxy in component_proxy_map.items():
        quote = component_quotes.get(proxy.index_code or "")
        if quote is not None and quote.error is None:
            quote_cache[key] = quote
        else:
            if quote is not None:
                quote_cache[key] = quote
            component_failures.append(proxy)

    if component_failures:
        fallback_quotes = fetch_index_quotes_for_mappings(
            component_failures,
            timeout=timeout,
        )
        for proxy in component_failures:
            key = (proxy.tencent_symbol, proxy.xueqiu_symbol)
            fallback = fallback_quotes.get(key)
            if fallback is not None and fallback.error is None:
                quote_cache[key] = fallback

    results: list[EstimatedNavResult] = []

    for code in codes:
        nav = official_navs.get(code)
        mapping = mapping_candidates.get(code)
        proxy = proxy_mappings.get(code)

        if nav is None or not nav.available or nav.nav is None or nav.nav_date is None:
            results.append(
                _unavailable(
                    fund_code=code,
                    proxy_id=(proxy.index_code if proxy else None),
                    error="MISSING_OFFICIAL_NAV",
                )
            )
            continue

        if proxy is None or proxy.status != "RESOLVED" or not proxy.index_code:
            results.append(
                _unavailable(
                    fund_code=code,
                    proxy_id=None,
                    error="UNRESOLVED_INDEX_PROXY",
                )
            )
            continue

        quote_key = (proxy.tencent_symbol, proxy.xueqiu_symbol)
        quote = quote_cache.get(quote_key)
        if quote is None:
            results.append(
                _unavailable(
                    fund_code=code,
                    proxy_id=proxy.index_code,
                    error="MISSING_BATCH_INDEX_QUOTE",
                )
            )
            continue

        # F10 benchmark exposure is an accuracy enhancement, not a hard
        # prerequisite. Missing exposure falls back to 1.0 in the generic
        # resolver and caps quality at MEDIUM.
        exposure = (
            mapping.exposure_ratio_candidate
            if mapping is not None
            else None
        )

        results.append(
            resolve_r1_from_previous_close(
                fund_code=code,
                official_nav=nav.nav,
                official_nav_date=nav.nav_date,
                expected_anchor_date=expected_anchor_date,
                index_quote=quote,
                as_of=as_of,
                exposure_ratio=exposure,
                max_proxy_age_seconds=max_proxy_age_seconds,
            )
        )

    return results
