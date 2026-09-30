from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Iterable

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

    quote_cache = fetch_index_quotes_for_mappings(
        unique_proxy_map.values(),
        timeout=timeout,
    )
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

        is_target_etf = proxy.source == "R1_TARGET_ETF_OVERRIDE"
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
                resolver_method=(
                    "TARGET_ETF_PREV_CLOSE"
                    if is_target_etf
                    else "INDEX_PROXY_PREV_CLOSE"
                ),
                quality_cap="MEDIUM" if is_target_etf else None,
            )
        )

    return results
