from datetime import datetime
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from runtime.lof.commodity_quote import parse_eastmoney_commodity_quote

TZ = ZoneInfo("Asia/Shanghai")


class CommodityQuoteTest(unittest.TestCase):
    def test_falls_back_to_utime_when_tjsrq_is_zero(self) -> None:
        quote = parse_eastmoney_commodity_quote(
            {"qt": {
                "p": 4185.2,
                "fzjsj": 4186.7,
                "tjsrq": 0,
                "jysj": 74455,
                "utime": 1790811895,
            }},
            code="GC00Y",
        )
        self.assertEqual(quote.current, Decimal("4185.2"))
        self.assertEqual(
            quote.quote_time,
            datetime(2026, 10, 1, 7, 44, 55, tzinfo=TZ),
        )
        self.assertIsNone(quote.error)

    def test_missing_time_stays_unavailable(self) -> None:
        quote = parse_eastmoney_commodity_quote(
            {"qt": {"p": 1, "tjsrq": 0, "jysj": 0, "utime": 0}},
            code="TEST",
        )
        self.assertEqual(
            quote.error,
            "INVALID_OR_MISSING_COMMODITY_QUOTE",
        )


if __name__ == "__main__":
    unittest.main()
