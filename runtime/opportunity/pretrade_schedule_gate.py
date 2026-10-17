from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Collection

PRE_TRADE = "PRE_TRADE"
SAME_DAY_CLOSE_REFRESH = "SAME_DAY_CLOSE_REFRESH"
CATCH_UP_REFRESH = "CATCH_UP_REFRESH"
SKIP = "SKIP"

CLOSE = "CLOSE"
PRE_TRADE_CLOSE = "PRE_TRADE_CLOSE"


@dataclass(frozen=True)
class ScheduleDecision:
    today: date
    tomorrow: date
    today_is_trade_day: bool
    tomorrow_is_trade_day: bool
    latest_completed_trade_day: date
    last_successful_notice_scan_date: date | None
    freshness_lag_detected: bool
    information_target_date: date
    run_intent: str
    market_snapshot_mode: str | None


def market_snapshot_mode_for(run_intent: str) -> str | None:
    if run_intent == SAME_DAY_CLOSE_REFRESH:
        return CLOSE
    if run_intent in {PRE_TRADE, CATCH_UP_REFRESH}:
        return PRE_TRADE_CLOSE
    if run_intent == SKIP:
        return None
    raise ValueError(f"unsupported run_intent={run_intent}")


def decide_schedule(
    today: date,
    trade_dates: Collection[date],
    *,
    last_successful_notice_scan_date: date | None = None,
) -> ScheduleDecision:
    """Classify the nightly convertible run without conflating responsibilities.

    PRE_TRADE prepares for the next trading day from the latest completed close,
    so it must use PRE_TRADE_CLOSE rather than today's CLOSE gate.
    SAME_DAY_CLOSE_REFRESH freezes the just-completed trading day's CLOSE.
    CATCH_UP_REFRESH repairs a missed completed trading day using PRE_TRADE_CLOSE.
    SKIP is allowed only when there is no trading-day work and no freshness lag.
    """
    dates = set(trade_dates)
    eligible = [d for d in dates if d <= today]
    if not eligible:
        raise ValueError("trade calendar has no completed date on or before today")

    tomorrow = today + timedelta(days=1)
    today_trade = today in dates
    tomorrow_trade = tomorrow in dates
    latest_completed = max(eligible)
    freshness_lag = (
        last_successful_notice_scan_date is None
        or last_successful_notice_scan_date < latest_completed
    )

    if tomorrow_trade:
        run_intent = PRE_TRADE
        information_target = today
    elif today_trade:
        run_intent = SAME_DAY_CLOSE_REFRESH
        information_target = today
    elif freshness_lag:
        run_intent = CATCH_UP_REFRESH
        information_target = latest_completed
    else:
        run_intent = SKIP
        information_target = latest_completed

    return ScheduleDecision(
        today=today,
        tomorrow=tomorrow,
        today_is_trade_day=today_trade,
        tomorrow_is_trade_day=tomorrow_trade,
        latest_completed_trade_day=latest_completed,
        last_successful_notice_scan_date=last_successful_notice_scan_date,
        freshness_lag_detected=freshness_lag,
        information_target_date=information_target,
        run_intent=run_intent,
        market_snapshot_mode=market_snapshot_mode_for(run_intent),
    )
