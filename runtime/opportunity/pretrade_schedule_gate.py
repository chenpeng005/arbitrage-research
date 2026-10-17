from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Collection

PRE_TRADE = "PRE_TRADE"
SAME_DAY_CLOSE_REFRESH = "SAME_DAY_CLOSE_REFRESH"
SKIP = "SKIP"


@dataclass(frozen=True)
class ScheduleDecision:
    today: date
    tomorrow: date
    today_is_trade_day: bool
    tomorrow_is_trade_day: bool
    latest_completed_trade_day: date
    run_intent: str


def decide_schedule(today: date, trade_dates: Collection[date]) -> ScheduleDecision:
    """Classify the nightly convertible run without conflating two responsibilities.

    PRE_TRADE exists to prepare for the next trading day.
    SAME_DAY_CLOSE_REFRESH exists to make the just-completed trading day's formal
    facts fresh even when the next calendar day is closed (for example Friday).
    SKIP is allowed only when neither today nor tomorrow is a trading day.
    """
    dates = set(trade_dates)
    eligible = [d for d in dates if d <= today]
    if not eligible:
        raise ValueError("trade calendar has no completed date on or before today")

    tomorrow = today + timedelta(days=1)
    today_trade = today in dates
    tomorrow_trade = tomorrow in dates

    if tomorrow_trade:
        run_intent = PRE_TRADE
    elif today_trade:
        run_intent = SAME_DAY_CLOSE_REFRESH
    else:
        run_intent = SKIP

    return ScheduleDecision(
        today=today,
        tomorrow=tomorrow,
        today_is_trade_day=today_trade,
        tomorrow_is_trade_day=tomorrow_trade,
        latest_completed_trade_day=max(eligible),
        run_intent=run_intent,
    )
