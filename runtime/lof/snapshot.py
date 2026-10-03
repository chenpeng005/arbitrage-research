from __future__ import annotations

from datetime import date, datetime
from typing import Iterable
from uuid import uuid4

from .classification import FundTypeRecord
from .estimate_model_registry import estimate_model_id, estimate_model_version
from .nav import OfficialNavRecord, nav_age_days
from .premium import premium_rate
from .quote import QuoteRecord, is_quote_stale, quote_age_seconds
from .resolver import EstimatedNavResult
from .state import FundTradeStateRecord
from .universe import LofIdentity


CONTRACT_VERSION = "LOF_MARKET_SNAPSHOT_V1"


def _key(exchange: str, code: str) -> tuple[str, str]:
    return exchange, code


def _official_nav_lag_label(
    *,
    nav_date: date | None,
    as_of_date: date,
    previous_trading_day: date | None,
    second_previous_trading_day: date | None,
) -> str | None:
    if nav_date is None:
        return None
    if nav_date == as_of_date:
        return "T-0"
    if previous_trading_day is not None and nav_date == previous_trading_day:
        return "T-1"
    if (
        second_previous_trading_day is not None
        and nav_date == second_previous_trading_day
    ):
        return "T-2"
    if (
        second_previous_trading_day is not None
        and nav_date < second_previous_trading_day
    ):
        return "T-2+"
    return None


def build_market_snapshot(
    *,
    universe: Iterable[LofIdentity],
    quotes: Iterable[QuoteRecord],
    official_navs: Iterable[OfficialNavRecord],
    estimated_navs: Iterable[EstimatedNavResult] = (),
    trade_states: Iterable[FundTradeStateRecord] = (),
    generated_at: datetime,
    market_cutoff: datetime,
    max_quote_age_seconds: int,
    type_records: dict[tuple[str, str], FundTypeRecord] | None = None,
    previous_trading_day: date | None = None,
    second_previous_trading_day: date | None = None,
    snapshot_id: str | None = None,
) -> dict:
    universe_rows = list(universe)
    quote_map = {_key(x.exchange, x.code): x for x in quotes}
    nav_map = {_key(x.exchange, x.code): x for x in official_navs}
    estimated_nav_map = {x.fund_code: x for x in estimated_navs}
    state_map = {x.code: x for x in trade_states}
    type_records = type_records or {}

    rows: list[dict] = []
    quote_fresh_count = 0
    quote_stale_count = 0
    quote_unavailable_count = 0
    official_nav_available_count = 0
    official_nav_unavailable_count = 0
    estimated_nav_available_count = 0
    estimated_nav_stale_count = 0
    estimated_nav_unavailable_count = 0
    state_available_count = 0
    state_unavailable_count = 0

    as_of_date: date = market_cutoff.date()

    for identity in universe_rows:
        key = _key(identity.exchange, identity.code)
        quote = quote_map.get(key)
        nav = nav_map.get(key)
        estimated = estimated_nav_map.get(identity.code)
        state = state_map.get(identity.code)
        type_record = type_records.get(key)

        if quote is None or not quote.available:
            quote_status = "UNAVAILABLE"
            quote_age = None
            quote_unavailable_count += 1
            price = None
            quote_time = None
            pct_change = None
            volume = None
            amount = None
            bid1_price = None
            bid1_volume = None
            bid2_price = None
            bid2_volume = None
            ask1_price = None
            ask1_volume = None
            ask2_price = None
            ask2_volume = None
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
            bid1_price = quote.bid1_price
            bid1_volume = quote.bid1_volume
            bid2_price = quote.bid2_price
            bid2_volume = quote.bid2_volume
            ask1_price = quote.ask1_price
            ask1_volume = quote.ask1_volume
            ask2_price = quote.ask2_price
            ask2_volume = quote.ask2_volume
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

        if estimated is None:
            estimated_nav = None
            resolver_class = None
            estimated_nav_time = None
            estimated_nav_source = None
            estimated_nav_method = None
            estimated_model_id = None
            estimated_model_version = None
            estimated_nav_quality = "UNKNOWN"
            estimated_nav_status = "UNAVAILABLE"
            estimated_nav_age = None
            estimated_nav_proxy = None
            estimated_nav_proxy_time = None
            estimated_nav_proxy_return = None
            estimated_nav_fx_return = None
            estimated_nav_exposure_ratio = None
            estimated_nav_tracking_adjustment = None
            estimated_premium = None
            estimated_nav_unavailable_count += 1
        else:
            estimated_nav = estimated.estimated_nav
            resolver_class = estimated.resolver_class
            estimated_nav_time = estimated.estimated_nav_time
            estimated_nav_source = "LOF_RESOLVER"
            estimated_nav_method = estimated.resolver_method
            estimated_model_id = estimate_model_id(estimated_nav_method)
            estimated_model_version = estimate_model_version(
                estimated_nav_method
            )
            estimated_nav_quality = estimated.estimated_nav_quality
            estimated_nav_status = estimated.estimated_nav_status
            estimated_nav_proxy = estimated.proxy_id
            estimated_nav_proxy_time = estimated.proxy_time
            estimated_nav_proxy_return = estimated.proxy_return
            estimated_nav_fx_return = estimated.fx_return
            estimated_nav_exposure_ratio = estimated.exposure_ratio_used
            estimated_nav_tracking_adjustment = estimated.tracking_adjustment_used
            if estimated_nav_time is None:
                estimated_nav_age = None
            else:
                estimated_nav_age = max(
                    0,
                    int((market_cutoff - estimated_nav_time).total_seconds()),
                )
            if estimated_nav_status == "AVAILABLE":
                estimated_nav_available_count += 1
                estimated_premium = premium_rate(price, estimated_nav)
            elif estimated_nav_status == "STALE":
                estimated_nav_stale_count += 1
                estimated_premium = None
            else:
                estimated_nav_unavailable_count += 1
                estimated_premium = None

        if state is None or state.error is not None:
            state_available = False
            state_unavailable_count += 1
        else:
            state_available = True
            state_available_count += 1

        static_premium = premium_rate(price, official_nav)
        official_nav_lag_label = _official_nav_lag_label(
            nav_date=official_nav_date,
            as_of_date=as_of_date,
            previous_trading_day=previous_trading_day,
            second_previous_trading_day=second_previous_trading_day,
        )
        if estimated_premium is not None:
            display_premium = estimated_premium
            display_premium_basis = "ESTIMATED_NAV"
        elif static_premium is not None:
            display_premium = static_premium
            display_premium_basis = "OFFICIAL_NAV"
        else:
            display_premium = None
            display_premium_basis = "UNAVAILABLE"

        effective_daily_subscription_limit = (
            None
            if (
                state_available
                and state.subscription_status == "SUSPENDED"
            )
            else (
                state.daily_subscription_limit
                if state_available
                else None
            )
        )

        row = {
            "code": identity.code,
            "name": identity.name,
            "exchange": identity.exchange,
            "lof_type": (
                type_record.lof_type if type_record is not None else "UNKNOWN"
            ),
            "fund_type_raw": (
                type_record.fund_type_raw if type_record is not None else None
            ),
            "type_source": (
                type_record.source if type_record is not None else None
            ),
            "type_error": (
                type_record.error if type_record is not None else "UNAVAILABLE"
            ),
            "price": price,
            "quote_time": quote_time,
            "pct_change": pct_change,
            "volume": volume,
            "amount": amount,
            "bid1_price": bid1_price,
            "bid1_volume": bid1_volume,
            "bid2_price": bid2_price,
            "bid2_volume": bid2_volume,
            "ask1_price": ask1_price,
            "ask1_volume": ask1_volume,
            "ask2_price": ask2_price,
            "ask2_volume": ask2_volume,
            "quote_source": quote_source,
            "quote_status": quote_status,
            "quote_age_seconds": quote_age,
            "official_nav": official_nav,
            "official_nav_date": official_nav_date,
            "official_nav_source": official_nav_source,
            "official_nav_status": official_nav_status,
            "official_nav_age_days": official_nav_age,
            "resolver_class": resolver_class,
            "estimated_nav": estimated_nav,
            "estimated_nav_time": estimated_nav_time,
            "estimated_nav_source": estimated_nav_source,
            "estimated_nav_method": estimated_nav_method,
            "estimated_model_id": estimated_model_id,
            "estimated_model_version": estimated_model_version,
            "estimated_nav_quality": estimated_nav_quality,
            "estimated_nav_status": estimated_nav_status,
            "estimated_nav_age_seconds": estimated_nav_age,
            "estimated_nav_proxy": estimated_nav_proxy,
            "estimated_nav_proxy_time": estimated_nav_proxy_time,
            "estimated_nav_proxy_return": estimated_nav_proxy_return,
            "estimated_nav_fx_return": estimated_nav_fx_return,
            "estimated_nav_exposure_ratio": estimated_nav_exposure_ratio,
            "estimated_nav_tracking_adjustment": estimated_nav_tracking_adjustment,
            "bid1_estimated_premium_rate": (
                premium_rate(bid1_price, estimated_nav)
                if (
                    quote_status == "FRESH"
                    and estimated_nav_status == "AVAILABLE"
                )
                else None
            ),
            "bid2_estimated_premium_rate": (
                premium_rate(bid2_price, estimated_nav)
                if (
                    quote_status == "FRESH"
                    and estimated_nav_status == "AVAILABLE"
                )
                else None
            ),
            "static_premium_rate": static_premium,
            "estimated_premium_rate": estimated_premium,
            "display_premium_rate": display_premium,
            "display_premium_basis": display_premium_basis,
            "official_nav_lag_label": official_nav_lag_label,
            "subscription_status": (
                state.subscription_status if state_available else "UNKNOWN"
            ),
            "redemption_status": (
                state.redemption_status if state_available else "UNKNOWN"
            ),
            "daily_subscription_limit": effective_daily_subscription_limit,
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
            # Reference for the brokerage on-market LOF subscription path:
            # ChinaClear/SZSE rules make subscribed on-market shares usable
            # from the day after confirmation. This is a route reference,
            # not a broker-specific verified execution fact.
            "onsite_subscription_to_sell_days_reference": (
                state.subscription_confirmation_days + 1
                if (
                    state_available
                    and state.subscription_confirmation_days is not None
                )
                else None
            ),
            "onsite_sell_day_reference_source": (
                "CHINACLEAR_LOF_ONMARKET_RULE"
                if (
                    state_available
                    and state.subscription_confirmation_days is not None
                )
                else None
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
        "estimated_nav_available_count": estimated_nav_available_count,
        "estimated_nav_stale_count": estimated_nav_stale_count,
        "estimated_nav_unavailable_count": estimated_nav_unavailable_count,
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
        estimated_status = row.get("estimated_nav_status")
        if estimated_premium is not None and (
            price is None
            or estimated_nav is None
            or estimated_status != "AVAILABLE"
        ):
            raise ValueError(f"estimated premium lacks valid inputs: {key}")
        if estimated_status == "AVAILABLE":
            if row.get("estimated_nav_time") is None:
                raise ValueError(f"available estimated nav lacks time: {key}")
            if row.get("estimated_nav_method") is None:
                raise ValueError(f"available estimated nav lacks method: {key}")
            if row.get("estimated_nav_source") is None:
                raise ValueError(f"available estimated nav lacks source: {key}")

        display_premium = row.get("display_premium_rate")
        display_basis = row.get("display_premium_basis")
        if display_basis == "ESTIMATED_NAV":
            if display_premium != estimated_premium or estimated_premium is None:
                raise ValueError(f"invalid estimated display premium: {key}")
        elif display_basis == "OFFICIAL_NAV":
            if display_premium != static_premium or static_premium is None:
                raise ValueError(f"invalid official-nav display premium: {key}")
        elif display_basis == "UNAVAILABLE":
            if display_premium is not None:
                raise ValueError(f"unavailable display premium has value: {key}")
        else:
            raise ValueError(f"invalid display premium basis: {key}")

        if (
            row.get("subscription_status") == "SUSPENDED"
            and row.get("daily_subscription_limit") is not None
        ):
            raise ValueError(f"suspended subscription exposes limit: {key}")

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
