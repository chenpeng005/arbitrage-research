from __future__ import annotations

import unittest
from datetime import date

from runtime.opportunity.pretrade_schedule_gate import (
    PRE_TRADE,
    SAME_DAY_CLOSE_REFRESH,
    SKIP,
    decide_schedule,
)


class PretradeScheduleGateTests(unittest.TestCase):
    def test_friday_trade_day_refreshes_even_when_saturday_closed(self) -> None:
        friday = date(2026, 10, 16)
        decision = decide_schedule(friday, {friday})
        self.assertTrue(decision.today_is_trade_day)
        self.assertFalse(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, friday)
        self.assertEqual(decision.run_intent, SAME_DAY_CLOSE_REFRESH)

    def test_saturday_with_no_sunday_or_monday_context_skips(self) -> None:
        friday = date(2026, 10, 16)
        saturday = date(2026, 10, 17)
        decision = decide_schedule(saturday, {friday})
        self.assertFalse(decision.today_is_trade_day)
        self.assertFalse(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, friday)
        self.assertEqual(decision.run_intent, SKIP)

    def test_sunday_before_monday_keeps_pretrade_behavior(self) -> None:
        friday = date(2026, 10, 16)
        sunday = date(2026, 10, 18)
        monday = date(2026, 10, 19)
        decision = decide_schedule(sunday, {friday, monday})
        self.assertFalse(decision.today_is_trade_day)
        self.assertTrue(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, friday)
        self.assertEqual(decision.run_intent, PRE_TRADE)

    def test_trade_day_before_another_trade_day_is_pretrade(self) -> None:
        monday = date(2026, 10, 19)
        tuesday = date(2026, 10, 20)
        decision = decide_schedule(monday, {monday, tuesday})
        self.assertTrue(decision.today_is_trade_day)
        self.assertTrue(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, monday)
        self.assertEqual(decision.run_intent, PRE_TRADE)

    def test_calendar_without_completed_trade_day_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            decide_schedule(date(2026, 1, 1), {date(2026, 1, 2)})


if __name__ == "__main__":
    unittest.main()
