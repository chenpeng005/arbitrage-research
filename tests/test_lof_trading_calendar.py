from __future__ import annotations

import unittest
from datetime import date
from io import BytesIO
from unittest.mock import patch
from urllib.error import HTTPError

from runtime.lof.trading_calendar import (
    fetch_previous_trading_day,
    parse_last_market_trade_date,
    parse_trading_dates,
    previous_trading_day_from_dates,
)


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self.body


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

    def test_parse_last_market_trade_date(self) -> None:
        fields = [""] * 31
        fields[30] = "20260930161500"
        text = 'v_sh000001="' + "~".join(fields) + '";'
        self.assertEqual(
            parse_last_market_trade_date(text, symbol="sh000001"),
            date(2026, 9, 30),
        )

    @patch("runtime.lof.trading_calendar.urlopen")
    def test_http_calendar_failure_uses_last_quote_date_on_holiday(
        self,
        urlopen_mock,
    ) -> None:
        quote_fields = [""] * 31
        quote_fields[30] = "20260930161500"
        quote = (
            'v_sh000001="' + "~".join(quote_fields) + '";'
        ).encode("gb18030")

        urlopen_mock.side_effect = [
            HTTPError("https://kline", 501, "Not Implemented", None, None),
            FakeResponse(quote),
        ]
        self.assertEqual(
            fetch_previous_trading_day(
                as_of=date(2026, 10, 1),
                timeout=1,
            ),
            date(2026, 9, 30),
        )

    @patch("runtime.lof.trading_calendar.urlopen")
    def test_fallback_fails_closed_when_quote_date_equals_as_of(
        self,
        urlopen_mock,
    ) -> None:
        quote_fields = [""] * 31
        quote_fields[30] = "20261009100000"
        quote = (
            'v_sh000001="' + "~".join(quote_fields) + '";'
        ).encode("gb18030")
        urlopen_mock.side_effect = [
            HTTPError("https://kline", 501, "Not Implemented", None, None),
            FakeResponse(quote),
        ]
        with self.assertRaisesRegex(
            RuntimeError,
            "PREVIOUS_TRADING_DAY_UNAVAILABLE",
        ):
            fetch_previous_trading_day(
                as_of=date(2026, 10, 9),
                timeout=1,
            )


if __name__ == "__main__":
    unittest.main()
