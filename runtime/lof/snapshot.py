from __future__ import annotations

from datetime import date, datetime
from typing import Iterable
from uuid import uuid4

from .nav import OfficialNavRecord, nav_age_days
from .premium import premium_rate
from .quote import QuoteRecord, is_quote_stale, quote_age_seconds
from .state import FundTradeStateRecord
from .universe import LofIdentity


CONTRACT_VERSION = "LOF_MARKET_SNAPSHOT_V1"


def _key(exchange: str, code: str) -> tuple[str, str]:
    return exchange, code


def build_market_snapshot(
    *,
    universe: Iterable[LofIdentity],
    quotes: Iterable[QuoteRecord],
    official_navs: Iterable[OfficialNavRecord],
    trade_states: Iterable[FundTradeStateRecord] = (),
    generated_at: datetime,
    market_cutoff: datetime,
    max_quote_age_seconds: int,
    lof_types: dict[tuple[str, str], str] | None = None,
    snapshot_id: str | None = None,
) -> dict:
    universe_rows = list(universe)
    quote_map = {_key(x.exchange, x.code): x for x in quotes}
    nav_map = {_key(x.exchange, x.code): x for x in official_navs}
    state_map = {x.code: x for x in trade_states}
    lof_types = lof_types or {}

    rows: list[dict] = []
    quote_fresh_count = 0
    quote_stale_count = 0
    quote_unavailable_count = 0
    official_nav_available_count = 0
    official_nav_unavailable_count = 0
    state_available_count = 0
    state_unavailable_count = 0

    as_of_date: date = market_cutoff.date()

    for identity in universe_rows:
        key = _key(identity.exchange, identity.code)
        quote = quote_map.get(key)
        nav = nav_map.get(key)
        state = state_map.get(identity.code)

        if quote is None or not quote.available:
            quote_status = "UNAVAILABLE"
            quote_age = None
            quote_unavailable_count += 1
            price = None
            quote_time = None
            pct_change = None
            volume = None
            amount = None
            quote_source = quote.source if quote is not None else None
        else:
            quote_age = quote_age_seconds(quote, now=market_cutoff)
            quote_status = (
                "STALE"
                if is_quote_stale(
                    quote,
                    now=market_cutoff,
                    max_age_seconds=max_quote_age_seconds,
                )
                else "FRESH"
            )
            if quote_status == "FRESH":
                quote_fresh_count += 1
            else:
                quote_stale_count += 1
            price = quote.price
            quote_time = quote.quote_time
            pct_change = quote.pct_change
            volume = quote.volume
            amount = quote.amount
            quote_source = quote.source

        if nav is None or not nav.available:
            official_nav_status = "UNAVAILABLE"
            official_nav = None
            official_nav_date = None
            official_nav_age = None
            official_nav_source = nav.source if nav is not None else None
            official_nav_unavailable_count += 1
        else:
            official_nav_status = "AVAILABLE"
            official_nav = nav.nav
            official_nav_date = nav.nav_date
            official_nav_age = nav_age_days(nav, as_of=as_of_date)
            official_nav_source = nav.source
            official_nav_available_count += 1

        if state is None or state.error is not None:
            state_available = False
            state_unavailable_count += 1
        else:
            state_available = True
            state_available_count += 1

        row = {
            "code": identity.code,
            "name": identity.name,
            "exchange": identity.exchange,
            "lof_type": lof_types.get(key, "UNKNOWN"),
            "price": price,
            "quote_time": quote_time,
            "pct_change": pct_change,
            "volume": volume,
            "amount": amount,
            "quote_source": quote_source,
            "quote_status": quote_status,
            "quote_age_seconds": quote_age,
            "official_nav": official_nav,
            "official_nav_date": official_nav_date,
            "official_nav_source": official_nav_source,
            "official_nav_status": official_nav_status,
            "official_nav_age_days": official_nav_age,
            "estimated_nav": None,
            "estimated_nav_time": None,
            "estimated_nav_source": None,
            "estimated_nav_status": "UNAVAILABLE",
            "static_premium_rate": premium_rate(price, official_nav),
            "estimated_premium_rate": None,
            "subscription_status": (
                state.subscription_status if state_available else "UNKNOWN"
            ),
            "redemption_status": (
                state.redemption_status if state_available else "UNKNOWN"
            ),
            "daily_subscription_limit": (
                state.daily_subscription_limit if state_available else None
            ),
            "minimum_subscription_amount": (
                state.minimum_subscription_amount if state_available else None
            ),
            "limit_scope": (
                state.limit_scope if state_available else "UNKNOWN"
            ),
            "subscription_confirmation_days": (
                state.subscription_confirmation_days if state_available else None
            ),
            "subscription_to_sell_days": (
                state.subscription_to_sell_days if state_available else None
            ),
            "subscription_fee_reference": (
                list(state.subscription_fee_schedule) if state_available else None
            ),
            "subscription_fee_source": (
                state.fee_source if state_available else None
            ),
            "redemption_fee_reference": (
                list(state.redemption_fee_schedule) if state_available else None
            ),
            "state_source": (
                state.state_source if state_available else None
            ),
            "state_time": (
                state.fetched_at if state_available else None
            ),
            "opportunity_path": None,
            "opportunity_state": None,
            "estimated_net_profit": None,
            "opportunity_note": None,
        }
        rows.append(row)

    rows.sort(key=lambda x: (x["exchange"], x["code"]))

    quality_summary = {
        "quote_fresh_count": quote_fresh_count,
        "quote_stale_count": quote_stale_count,
        "quote_unavailable_count": quote_unavailable_count,
        "official_nav_available_count": official_nav_available_count,
        "official_nav_unavailable_count": official_nav_unavailable_count,
        "estimated_nav_available_count": 0,
        "state_available_count": state_available_count,
        "state_unavailable_count": state_unavailable_count,
        "row_count": len(rows),
    }

    snapshot = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": snapshot_id or f"lof-{uuid4()}",
        "generated_at": generated_at,
        "market_cutoff": market_cutoff,
        "universe_count": len(universe_rows),
        "rows": rows,
        "quality_summary": quality_summary,
    }
    validate_market_snapshot(snapshot, max_quote_age_seconds=max_quote_age_seconds)
    return snapshot


def validate_market_snapshot(
    snapshot: dict,
    *,
    max_quote_age_seconds: int,
) -> None:
    if snapshot.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("invalid contract_version")

    rows = snapshot.get("rows")
    if not isinstance(rows, list):
        raise ValueError("rows must be a list")

    universe_count = snapshot.get("universe_count")
    if universe_count != len(rows):
        raise ValueError("row_count must equal universe_count")

    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = _key(str(row.get("exchange")), str(row.get("code")))
        if key in seen:
            raise ValueError(f"duplicate LOF identity: {key}")
        seen.add(key)

        price = row.get("price")
        quote_time = row.get("quote_time")
        quote_status = row.get("quote_status")
        quote_age = row.get("quote_age_seconds")

        if price is not None and quote_time is None:
            raise ValueError(f"price without quote_time: {key}")

        if quote_status == "FRESH":
            if quote_age is None or quote_age > max_quote_age_seconds:
                raise ValueError(f"fresh quote exceeds age limit: {key}")

        official_nav = row.get("official_nav")
        official_nav_date = row.get("official_nav_date")
        if official_nav is not None:
            if official_nav <= 0 or official_nav_date is None:
                raise ValueError(f"invalid official nav: {key}")

        static_premium = row.get("static_premium_rate")
        if static_premium is not None and (
            price is None or official_nav is None
        ):
            raise ValueError(f"static premium lacks inputs: {key}")

        estimated_premium = row.get("estimated_premium_rate")
        estimated_nav = row.get("estimated_nav")
        if estimated_premium is not None and (
            price is None or estimated_nav is None
        ):
            raise ValueError(f"estimated premium lacks inputs: {key}")

    summary = snapshot.get("quality_summary") or {}
    if summary.get("row_count") != len(rows):
        raise ValueError("quality_summary.row_count mismatch")

    quote_total = (
        int(summary.get("quote_fresh_count") or 0)
        + int(summary.get("quote_stale_count") or 0)
        + int(summary.get("quote_unavailable_count") or 0)
    )
    if quote_total != len(rows):
        raise ValueError("quote quality counts do not cover universe")

    nav_total = (
        int(summary.get("official_nav_available_count") or 0)
        + int(summary.get("official_nav_unavailable_count") or 0)
    )
    if nav_total != len(rows):
        raise ValueError("official NAV quality counts do not cover universe")

    state_total = (
        int(summary.get("state_available_count") or 0)
        + int(summary.get("state_unavailable_count") or 0)
    )
    if state_total != len(rows):
        raise ValueError("trade state quality counts do not cover universe")
