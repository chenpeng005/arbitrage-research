from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.quote import (
    QuoteRecord,
    is_quote_stale,
    parse_tencent_quote_line,
    parse_tencent_quote_response,
    quote_age_seconds,
)


TZ = ZoneInfo("Asia/Shanghai")


class LofQuoteTest(unittest.TestCase):
    def test_parse_tencent_quote_line(self) -> None:
        fields = [""] * 88
        fields[1] = "全球芯片LOF"
        fields[2] = "501225"
        fields[3] = "3.849"
        fields[9] = "3.848"
        fields[10] = "120"
        fields[11] = "3.847"
        fields[12] = "80"
        fields[19] = "3.850"
        fields[20] = "90"
        fields[21] = "3.851"
        fields[22] = "60"
        fields[30] = "20260929113624"
        fields[32] = "-0.44"
        fields[35] = "3.849/24060/9243237"
        fields[36] = "24060"
        line = 'v_sh501225="' + "~".join(fields) + '"'

        row = parse_tencent_quote_line(line)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row.code, "501225")
        self.assertEqual(row.exchange, "SSE")
        self.assertEqual(row.price, Decimal("3.849"))
        self.assertEqual(row.pct_change, Decimal("-0.44"))
        self.assertEqual(row.volume, Decimal("24060"))
        self.assertEqual(row.amount, Decimal("9243237"))
        self.assertEqual(row.bid1_price, Decimal("3.848"))
        self.assertEqual(row.bid1_volume, Decimal("120"))
        self.assertEqual(row.bid2_price, Decimal("3.847"))
        self.assertEqual(row.bid2_volume, Decimal("80"))
        self.assertEqual(row.ask1_price, Decimal("3.850"))
        self.assertEqual(row.ask1_volume, Decimal("90"))
        self.assertEqual(row.ask2_price, Decimal("3.851"))
        self.assertEqual(row.ask2_volume, Decimal("60"))
        self.assertEqual(
            row.quote_time,
            datetime(2026, 9, 29, 11, 36, 24, tzinfo=TZ),
        )

    def test_response_parser_skips_noise(self) -> None:
        fields = [""] * 88
        fields[1] = "示例LOF"
        fields[2] = "161128"
        fields[3] = "7.331"
        fields[30] = "20260929113633"
        fields[35] = "7.331/62958/46121781"
        text = 'noise;v_sz161128="' + "~".join(fields) + '";'
        rows = parse_tencent_quote_response(text)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].exchange, "SZSE")

    def test_invalid_quote_is_explicit(self) -> None:
        fields = [""] * 88
        fields[1] = "示例"
        fields[2] = "161128"
        fields[3] = ""
        fields[30] = ""
        line = 'v_sz161128="' + "~".join(fields) + '"'
        row = parse_tencent_quote_line(line)
        assert row is not None
        self.assertEqual(row.error, "INVALID_OR_MISSING_QUOTE")
        self.assertFalse(row.available)

    def test_stale_uses_quote_timestamp(self) -> None:
        row = QuoteRecord(
            code="501225",
            exchange="SSE",
            name="全球芯片LOF",
            price=Decimal("3.849"),
            quote_time=datetime(2026, 9, 29, 11, 36, 24, tzinfo=TZ),
            pct_change=Decimal("-0.44"),
            volume=Decimal("24060"),
            amount=Decimal("9243237"),
            source="TENCENT_QUOTE",
        )
        now = datetime(2026, 9, 29, 11, 36, 54, tzinfo=TZ)
        self.assertEqual(quote_age_seconds(row, now=now), 30)
        self.assertFalse(is_quote_stale(row, now=now, max_age_seconds=30))
        self.assertTrue(is_quote_stale(row, now=now, max_age_seconds=29))


if __name__ == "__main__":
    unittest.main()
