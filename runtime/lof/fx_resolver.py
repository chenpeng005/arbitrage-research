from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from functools import lru_cache

from .fx import (
    FxDailyClose,
    fetch_tencent_fx_daily,
    fetch_tencent_fx_quote,
    fx_close_on,
)
from .fx_cross_source import (
    FxCrossSourceCalibration,
    calibrate_usdcny_sources,
    fetch_wscn_daily_fx,
)
from .wscn_market_proxy import fetch_wscn_market_proxy


TENCENT_USDCNY = "whUSDCNY"
WSCN_USDCNH = "USDCNH.OTC"


@dataclass(frozen=True)
class ResolvedFxInput:
    pair: str
    anchor_date: date
    anchor: Decimal | None
    current: Decimal | None
    quote_time: datetime | None
    source: str
    status: str
    quote_age_seconds: int | None
    error: str | None = None
    primary_quote_time: datetime | None = None
    fallback_quote_time: datetime | None = None
    fallback_raw_current: Decimal | None = None
    fallback_anchor: Decimal | None = None
    fallback_anchor_basis_bps: float | None = None
    calibration: FxCrossSourceCalibration | None = None


def _age_seconds(
    *,
    as_of: datetime,
    quote_time: datetime | None,
) -> int | None:
    if quote_time is None:
        return None
    return int((as_of - quote_time).total_seconds())


def _status_from_age(
    age: int | None,
    *,
    max_quote_age_seconds: int,
) -> str:
    if age is None or age < -300:
        return "UNAVAILABLE"
    if age <= max_quote_age_seconds:
        return "AVAILABLE"
    return "STALE"


def _primary_result(
    *,
    nav_date: date,
    anchor: Decimal | None,
    current: Decimal | None,
    quote_time: datetime | None,
    as_of: datetime,
    max_quote_age_seconds: int,
    error: str | None,
) -> ResolvedFxInput:
    age = _age_seconds(as_of=as_of, quote_time=quote_time)
    status = (
        "UNAVAILABLE"
        if anchor is None
        or anchor <= 0
        or current is None
        or current <= 0
        or error is not None
        else _status_from_age(
            age,
            max_quote_age_seconds=max_quote_age_seconds,
        )
    )
    effective_error = error
    if status == "UNAVAILABLE" and effective_error is None:
        effective_error = "PRIMARY_USDCNY_UNAVAILABLE"
    return ResolvedFxInput(
        pair="USD/CNY",
        anchor_date=nav_date,
        anchor=anchor,
        current=current,
        quote_time=quote_time,
        source="TENCENT_USDCNY_PRIMARY",
        status=status,
        quote_age_seconds=age,
        error=effective_error,
        primary_quote_time=quote_time,
    )


def _resolve_usdcny_uncached(
    *,
    nav_date: date,
    as_of: datetime,
    timeout: int,
    max_quote_age_seconds: int,
    history_count: int,
    min_common_count: int,
    max_median_abs_basis_bps: float,
    max_p90_abs_basis_bps: float,
    max_anchor_basis_bps: float,
) -> ResolvedFxInput:
    try:
        tencent_rows = fetch_tencent_fx_daily(
            TENCENT_USDCNY,
            count=max(history_count, 140),
            timeout=timeout,
        )
    except Exception:
        tencent_rows = []

    anchor = fx_close_on(tencent_rows, nav_date)

    try:
        primary_quote = fetch_tencent_fx_quote(
            TENCENT_USDCNY,
            timeout=timeout,
        )
        primary = _primary_result(
            nav_date=nav_date,
            anchor=anchor,
            current=primary_quote.current,
            quote_time=primary_quote.quote_time,
            as_of=as_of,
            max_quote_age_seconds=max_quote_age_seconds,
            error=primary_quote.error,
        )
    except Exception as exc:
        primary = _primary_result(
            nav_date=nav_date,
            anchor=anchor,
            current=None,
            quote_time=None,
            as_of=as_of,
            max_quote_age_seconds=max_quote_age_seconds,
            error=f"TENCENT_USDCNY_ERROR:{type(exc).__name__}",
        )

    # Fresh primary always wins. Fallback exists only to bridge a stale or
    # unavailable onshore CNY quote; it never silently replaces a fresh CNY.
    if primary.status == "AVAILABLE":
        return primary

    try:
        cnh_rows = fetch_wscn_daily_fx(
            prod_code=WSCN_USDCNH,
            count=history_count,
            timeout=timeout,
        )
        calibration = calibrate_usdcny_sources(
            tencent_rows=tencent_rows,
            wscn_rows=cnh_rows,
            min_common_count=min_common_count,
            max_median_abs_level_diff_bps=max_median_abs_basis_bps,
            max_p90_abs_level_diff_bps=max_p90_abs_basis_bps,
        )
        cnh_quote = fetch_wscn_market_proxy(
            WSCN_USDCNH,
            timeout=timeout,
        )
    except Exception as exc:
        # A stale-but-valid primary is still better than losing the estimate.
        if primary.current is not None and primary.anchor is not None:
            return primary
        return ResolvedFxInput(
            pair="USD/CNY",
            anchor_date=nav_date,
            anchor=anchor,
            current=None,
            quote_time=None,
            source="USDCNY_CNH_FALLBACK",
            status="UNAVAILABLE",
            quote_age_seconds=None,
            error=f"CNH_FALLBACK_FETCH_ERROR:{type(exc).__name__}",
            primary_quote_time=primary.quote_time,
        )

    cnh_anchor = fx_close_on(cnh_rows, nav_date)
    if calibration.status != "PASS":
        if primary.current is not None and primary.anchor is not None:
            return primary
        return ResolvedFxInput(
            pair="USD/CNY",
            anchor_date=nav_date,
            anchor=anchor,
            current=None,
            quote_time=cnh_quote.quote_time,
            source="USDCNY_CNH_FALLBACK",
            status="UNAVAILABLE",
            quote_age_seconds=_age_seconds(
                as_of=as_of,
                quote_time=cnh_quote.quote_time,
            ),
            error=calibration.error or "CNH_CALIBRATION_FAILED",
            primary_quote_time=primary.quote_time,
            fallback_quote_time=cnh_quote.quote_time,
            fallback_raw_current=cnh_quote.current,
            fallback_anchor=cnh_anchor,
            calibration=calibration,
        )

    if (
        anchor is None
        or anchor <= 0
        or cnh_anchor is None
        or cnh_anchor <= 0
    ):
        if primary.current is not None and primary.anchor is not None:
            return primary
        return ResolvedFxInput(
            pair="USD/CNY",
            anchor_date=nav_date,
            anchor=anchor,
            current=None,
            quote_time=cnh_quote.quote_time,
            source="USDCNY_CNH_FALLBACK",
            status="UNAVAILABLE",
            quote_age_seconds=_age_seconds(
                as_of=as_of,
                quote_time=cnh_quote.quote_time,
            ),
            error="CNH_OR_CNY_ANCHOR_UNAVAILABLE",
            primary_quote_time=primary.quote_time,
            fallback_quote_time=cnh_quote.quote_time,
            fallback_raw_current=cnh_quote.current,
            fallback_anchor=cnh_anchor,
            calibration=calibration,
        )

    anchor_basis_bps = abs(
        float(cnh_anchor / anchor - Decimal("1"))
    ) * 10000.0
    if anchor_basis_bps > max_anchor_basis_bps:
        if primary.current is not None and primary.anchor is not None:
            return primary
        return ResolvedFxInput(
            pair="USD/CNY",
            anchor_date=nav_date,
            anchor=anchor,
            current=None,
            quote_time=cnh_quote.quote_time,
            source="USDCNY_CNH_FALLBACK",
            status="UNAVAILABLE",
            quote_age_seconds=_age_seconds(
                as_of=as_of,
                quote_time=cnh_quote.quote_time,
            ),
            error="CNH_ANCHOR_BASIS_TOO_WIDE",
            primary_quote_time=primary.quote_time,
            fallback_quote_time=cnh_quote.quote_time,
            fallback_raw_current=cnh_quote.current,
            fallback_anchor=cnh_anchor,
            fallback_anchor_basis_bps=anchor_basis_bps,
            calibration=calibration,
        )

    if (
        cnh_quote.error is not None
        or cnh_quote.current is None
        or cnh_quote.current <= 0
        or cnh_quote.quote_time is None
    ):
        if primary.current is not None and primary.anchor is not None:
            return primary
        return ResolvedFxInput(
            pair="USD/CNY",
            anchor_date=nav_date,
            anchor=anchor,
            current=None,
            quote_time=cnh_quote.quote_time,
            source="USDCNY_CNH_FALLBACK",
            status="UNAVAILABLE",
            quote_age_seconds=None,
            error=cnh_quote.error or "CNH_CURRENT_UNAVAILABLE",
            primary_quote_time=primary.quote_time,
            fallback_quote_time=cnh_quote.quote_time,
            fallback_raw_current=cnh_quote.current,
            fallback_anchor=cnh_anchor,
            fallback_anchor_basis_bps=anchor_basis_bps,
            calibration=calibration,
        )

    fallback_age = _age_seconds(
        as_of=as_of,
        quote_time=cnh_quote.quote_time,
    )
    fallback_status = _status_from_age(
        fallback_age,
        max_quote_age_seconds=max_quote_age_seconds,
    )
    if fallback_status == "UNAVAILABLE":
        if primary.current is not None and primary.anchor is not None:
            return primary

    # Normalize CNH onto the CNY anchor scale. This makes the applied FX
    # return equal to the relative CNH move since nav_date instead of mixing
    # a raw CNH level with a CNY anchor.
    normalized_current = cnh_quote.current * anchor / cnh_anchor

    # If both sources are stale, prefer the more recent calibrated source.
    if (
        primary.quote_time is not None
        and cnh_quote.quote_time <= primary.quote_time
        and primary.current is not None
        and primary.anchor is not None
    ):
        return primary

    return ResolvedFxInput(
        pair="USD/CNY",
        anchor_date=nav_date,
        anchor=anchor,
        current=normalized_current,
        quote_time=cnh_quote.quote_time,
        source="TENCENT_CNY_ANCHOR_WSCN_CNH_RETURN",
        status=fallback_status,
        quote_age_seconds=fallback_age,
        error=None if fallback_status != "UNAVAILABLE" else "CNH_FALLBACK_UNAVAILABLE",
        primary_quote_time=primary.quote_time,
        fallback_quote_time=cnh_quote.quote_time,
        fallback_raw_current=cnh_quote.current,
        fallback_anchor=cnh_anchor,
        fallback_anchor_basis_bps=anchor_basis_bps,
        calibration=calibration,
    )


@lru_cache(maxsize=64)
def _resolve_usdcny_cached(
    nav_date_iso: str,
    as_of_iso: str,
    timeout: int,
    max_quote_age_seconds: int,
    history_count: int,
) -> ResolvedFxInput:
    return _resolve_usdcny_uncached(
        nav_date=date.fromisoformat(nav_date_iso),
        as_of=datetime.fromisoformat(as_of_iso),
        timeout=timeout,
        max_quote_age_seconds=max_quote_age_seconds,
        history_count=history_count,
        min_common_count=60,
        max_median_abs_basis_bps=8.0,
        max_p90_abs_basis_bps=20.0,
        max_anchor_basis_bps=25.0,
    )


def resolve_usdcny_input(
    *,
    nav_date: date,
    as_of: datetime,
    timeout: int = 8,
    max_quote_age_seconds: int = 180,
    history_count: int = 180,
) -> ResolvedFxInput:
    return _resolve_usdcny_cached(
        nav_date.isoformat(),
        as_of.isoformat(),
        timeout,
        max_quote_age_seconds,
        history_count,
    )
