from __future__ import annotations

import unittest
from datetime import date

from runtime.lof.trading_calendar import (
    parse_trading_dates,
    previous_trading_day_from_dates,
)


class LofTradingCalendarTest(unittest.TestCase):
    def test_previous_day_skips_weekend_or_holiday_gap(self) -> None:
        dates = [
            date(2026, 9, 23),
            date(2026, 9, 24),
            date(2026, 9, 28),
            date(2026, 9, 29),
        ]
        self.assertEqual(
            previous_trading_day_from_dates(
                dates,
                as_of=date(2026, 9, 29),
            ),
            date(2026, 9, 28),
        )
        self.assertEqual(
            previous_trading_day_from_dates(
                dates,
                as_of=date(2026, 9, 28),
            ),
            date(2026, 9, 24),
        )

    def test_parse_kline_dates(self) -> None:
        payload = {
            "data": {
                "sh000001": {
                    "day": [
                        ["2026-09-24", "1", "1", "1", "1", "1"],
                        ["2026-09-28", "1", "1", "1", "1", "1"],
                        ["2026-09-29", "1", "1", "1", "1", "1"],
                    ]
                }
            }
        }
        self.assertEqual(
            parse_trading_dates(payload, symbol="sh000001"),
            [
                date(2026, 9, 24),
                date(2026, 9, 28),
                date(2026, 9, 29),
            ],
        )


if __name__ == "__main__":
    unittest.main()
