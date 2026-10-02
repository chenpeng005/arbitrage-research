from __future__ import annotations

from datetime import date
from io import BytesIO
import json
import unittest
from unittest.mock import patch

from runtime.lof.trading_calendar import (
    fetch_previous_trading_day,
    parse_sina_trading_dates,
)


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._payload


class TradingCalendarFallbackTests(unittest.TestCase):
    def test_parse_sina_dates(self):
        rows = parse_sina_trading_dates(
            [
                {"day": "2026-09-28"},
                {"day": "2026-09-29"},
                {"day": "bad"},
            ]
        )
        self.assertEqual(
            rows,
            [date(2026, 9, 28), date(2026, 9, 29)],
        )

    @patch("runtime.lof.trading_calendar.urlopen")
    def test_sina_history_recovers_second_previous_day(
        self,
        urlopen_mock,
    ):
        sina = json.dumps(
            [
                {"day": "2026-09-28", "close": "1"},
                {"day": "2026-09-29", "close": "1"},
                {"day": "2026-09-30", "close": "1"},
            ]
        ).encode("utf-8")
        urlopen_mock.side_effect = [
            OSError("tencent history unavailable"),
            _Response(sina),
        ]

        result = fetch_previous_trading_day(
            as_of=date(2026, 9, 30),
            timeout=1,
        )

        self.assertEqual(result, date(2026, 9, 29))
        self.assertEqual(urlopen_mock.call_count, 2)


if __name__ == "__main__":
    unittest.main()
