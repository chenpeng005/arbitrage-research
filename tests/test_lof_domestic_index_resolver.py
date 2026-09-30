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

    def test_target_etf_method_caps_quality_at_medium(self) -> None:
        quote = IndexQuote(
            symbol="sh562060",
            code="562060",
            name="标普A股红利ETF华宝",
            current=Decimal("0.611"),
            previous_close=Decimal("0.607"),
            quote_time=datetime(2026, 9, 30, 10, 30, tzinfo=TZ),
            source="TENCENT_QUOTE",
        )
        result = resolve_r1_from_previous_close(
            fund_code="501029",
            official_nav=Decimal("1.7270"),
            official_nav_date=date(2026, 9, 29),
            expected_anchor_date=date(2026, 9, 29),
            index_quote=quote,
            as_of=datetime(2026, 9, 30, 10, 30, 20, tzinfo=TZ),
            exposure_ratio=Decimal("0.95"),
            resolver_method="TARGET_ETF_PREV_CLOSE",
            quality_cap="MEDIUM",
        )
        self.assertEqual(result.estimated_nav_status, "AVAILABLE")
        self.assertEqual(result.estimated_nav_quality, "MEDIUM")
        self.assertEqual(result.resolver_method, "TARGET_ETF_PREV_CLOSE")
        self.assertEqual(result.proxy_id, "562060")

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
