from __future__ import annotations

import unittest
from datetime import date

from runtime.opportunity.pretrade_schedule_gate import (
    CATCH_UP_REFRESH,
    CLOSE,
    PRE_TRADE,
    PRE_TRADE_CLOSE,
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
        self.assertEqual(decision.market_snapshot_mode, CLOSE)

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
        self.assertEqual(decision.market_snapshot_mode, PRE_TRADE_CLOSE)

    def test_saturday_skips_after_friday_was_already_scanned(self) -> None:
        friday = date(2026, 10, 16)
        saturday = date(2026, 10, 17)
        decision = decide_schedule(
            saturday,
            {friday},
            last_successful_notice_scan_date=friday,
        )
        self.assertFalse(decision.freshness_lag_detected)
        self.assertEqual(decision.run_intent, SKIP)
        self.assertIsNone(decision.market_snapshot_mode)

    def test_missing_checkpoint_catches_up_instead_of_silent_skip(self) -> None:
        friday = date(2026, 10, 16)
        saturday = date(2026, 10, 17)
        decision = decide_schedule(saturday, {friday})
        self.assertTrue(decision.freshness_lag_detected)
        self.assertEqual(decision.information_target_date, friday)
        self.assertEqual(decision.run_intent, CATCH_UP_REFRESH)
        self.assertEqual(decision.market_snapshot_mode, PRE_TRADE_CLOSE)

    def test_sunday_before_monday_uses_latest_completed_close(self) -> None:
        thursday = date(2026, 10, 15)
        friday = date(2026, 10, 16)
        sunday = date(2026, 10, 18)
        monday = date(2026, 10, 19)
        decision = decide_schedule(
            sunday,
            {friday, monday},
            last_successful_notice_scan_date=thursday,
        )
        self.assertTrue(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.latest_completed_trade_day, friday)
        self.assertEqual(decision.information_target_date, sunday)
        self.assertEqual(decision.run_intent, PRE_TRADE)
        self.assertEqual(decision.market_snapshot_mode, PRE_TRADE_CLOSE)

    def test_trade_day_before_another_trade_day_uses_pretrade_close(self) -> None:
        monday = date(2026, 10, 19)
        tuesday = date(2026, 10, 20)
        decision = decide_schedule(
            monday,
            {monday, tuesday},
            last_successful_notice_scan_date=monday,
        )
        self.assertTrue(decision.today_is_trade_day)
        self.assertTrue(decision.tomorrow_is_trade_day)
        self.assertEqual(decision.information_target_date, monday)
        self.assertEqual(decision.run_intent, PRE_TRADE)
        self.assertEqual(decision.market_snapshot_mode, PRE_TRADE_CLOSE)

    def test_calendar_without_completed_trade_day_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            decide_schedule(date(2026, 1, 1), {date(2026, 1, 2)})


if __name__ == "__main__":
    unittest.main()
