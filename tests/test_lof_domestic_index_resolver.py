from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.domestic_index_resolver import resolve_r1_from_previous_close
from runtime.lof.index_quote import IndexQuote, parse_tencent_index_quote


TZ = ZoneInfo("Asia/Shanghai")


class LofDomesticIndexResolverTest(unittest.TestCase):
    def test_parse_index_quote(self) -> None:
        fields = [""] * 40
        fields[1] = "沪深300"
        fields[2] = "000300"
        fields[3] = "4342.07"
        fields[4] = "4340.76"
        fields[30] = "20260929120500"
        text = 'v_sh000300="' + "~".join(fields) + '";'
        row = parse_tencent_index_quote(text, symbol="sh000300")
        self.assertEqual(row.current, Decimal("4342.07"))
        self.assertEqual(row.previous_close, Decimal("4340.76"))

    def test_r1_fast_path(self) -> None:
        quote = IndexQuote(
            symbol="sh000300",
            code="000300",
            name="沪深300",
            current=Decimal("4342.07"),
            previous_close=Decimal("4340.76"),
            quote_time=datetime(2026, 9, 29, 12, 5, tzinfo=TZ),
            source="TENCENT_QUOTE",
        )
        result = resolve_r1_from_previous_close(
            fund_code="163407",
            official_nav=Decimal("2.6018"),
            official_nav_date=date(2026, 9, 28),
            expected_anchor_date=date(2026, 9, 28),
            index_quote=quote,
            as_of=datetime(2026, 9, 29, 12, 5, 10, tzinfo=TZ),
            exposure_ratio=Decimal("0.95"),
        )
        self.assertEqual(result.estimated_nav_status, "AVAILABLE")
        self.assertEqual(result.estimated_nav_quality, "HIGH")
        self.assertIsNotNone(result.estimated_nav)

    def test_wrong_nav_date_fails_closed(self) -> None:
        quote = IndexQuote(
            symbol="sh000300",
            code="000300",
            name="沪深300",
            current=Decimal("4342"),
            previous_close=Decimal("4340"),
            quote_time=datetime(2026, 9, 29, 12, 5, tzinfo=TZ),
            source="TENCENT_QUOTE",
        )
        result = resolve_r1_from_previous_close(
            fund_code="163407",
            official_nav=Decimal("2.60"),
            official_nav_date=date(2026, 9, 26),
            expected_anchor_date=date(2026, 9, 28),
            index_quote=quote,
            as_of=datetime(2026, 9, 29, 12, 5, 10, tzinfo=TZ),
            exposure_ratio=Decimal("0.95"),
        )
        self.assertEqual(result.estimated_nav_status, "UNAVAILABLE")
        self.assertEqual(result.error, "NAV_DATE_NOT_PREVIOUS_TRADING_DAY")


if __name__ == "__main__":
    unittest.main()
