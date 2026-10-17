from __future__ import annotations

import unittest
from datetime import date

from runtime.opportunity.pretrade_schedule_gate import (
    CATCH_UP_REFRESH,
    PRE_TRADE,
    SAME_DAY_CLOSE_REFRESH,
    SKIP,
    decide_schedule,
)


class PretradeScheduleGateTests(unittest.TestCase):
    def test_friday_trade_day_refreshes_even_when_saturday_closed(self) -> None:
        thursday = date(2026, 10, 15)
        friday = date(2026, 10, 16)
        decision = decide_schedule(
            friday,
            {friday},
            last_successful_notice_scan_date=thursday,
        )
        self.assertTrue(decision.today_is_trade_day)
        self.assertFalse(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, friday)
        self.assertEqual(decision.information_target_date, friday)
        self.assertEqual(decision.run_intent, SAME_DAY_CLOSE_REFRESH)

    def test_saturday_catches_up_missed_friday(self) -> None:
        thursday = date(2026, 10, 15)
        friday = date(2026, 10, 16)
        saturday = date(2026, 10, 17)
        decision = decide_schedule(
            saturday,
            {friday},
            last_successful_notice_scan_date=thursday,
        )
        self.assertFalse(decision.today_is_trade_day)
        self.assertFalse(decision.tomorrow_is_trade_day)
        self.assertTrue(decision.freshness_lag_detected)
        self.assertEqual(decision.latest_completed_trade_day, friday)
        self.assertEqual(decision.information_target_date, friday)
        self.assertEqual(decision.run_intent, CATCH_UP_REFRESH)

    def test_saturday_skips_after_friday_was_already_scanned(self) -> None:
        friday = date(2026, 10, 16)
        saturday = date(2026, 10, 17)
        decision = decide_schedule(
            saturday,
            {friday},
            last_successful_notice_scan_date=friday,
        )
        self.assertFalse(decision.today_is_trade_day)
        self.assertFalse(decision.tomorrow_is_trade_day)
        self.assertFalse(decision.freshness_lag_detected)
        self.assertEqual(decision.run_intent, SKIP)

    def test_missing_checkpoint_fails_open_to_catch_up_not_skip(self) -> None:
        friday = date(2026, 10, 16)
        saturday = date(2026, 10, 17)
        decision = decide_schedule(saturday, {friday})
        self.assertTrue(decision.freshness_lag_detected)
        self.assertEqual(decision.information_target_date, friday)
        self.assertEqual(decision.run_intent, CATCH_UP_REFRESH)

    def test_sunday_before_monday_keeps_pretrade_behavior(self) -> None:
        thursday = date(2026, 10, 15)
        friday = date(2026, 10, 16)
        sunday = date(2026, 10, 18)
        monday = date(2026, 10, 19)
        decision = decide_schedule(
            sunday,
            {friday, monday},
            last_successful_notice_scan_date=thursday,
        )
        self.assertFalse(decision.today_is_trade_day)
        self.assertTrue(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, friday)
        self.assertEqual(decision.information_target_date, sunday)
        self.assertEqual(decision.run_intent, PRE_TRADE)

    def test_trade_day_before_another_trade_day_is_pretrade(self) -> None:
        monday = date(2026, 10, 19)
        tuesday = date(2026, 10, 20)
        decision = decide_schedule(
            monday,
            {monday, tuesday},
            last_successful_notice_scan_date=monday,
        )
        self.assertTrue(decision.today_is_trade_day)
        self.assertTrue(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, monday)
        self.assertEqual(decision.information_target_date, monday)
        self.assertEqual(decision.run_intent, PRE_TRADE)

    def test_calendar_without_completed_trade_day_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            decide_schedule(date(2026, 1, 1), {date(2026, 1, 2)})


if __name__ == "__main__":
    unittest.main()
