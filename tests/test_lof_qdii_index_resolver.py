from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.fx import (
    parse_tencent_fx_daily,
    parse_tencent_fx_quote,
)
from runtime.lof.qdii_index_resolver import resolve_r3_qdii_index_bridge
from runtime.lof.us_history import parse_tencent_us_daily


TZ = ZoneInfo("Asia/Shanghai")


class LofR3QdiiIndexTest(unittest.TestCase):
    def test_parse_us_daily(self) -> None:
        payload = {
            "data": {
                "usXLK.AM": {
                    "qfqday": [
                        ["2026-09-24", "193.00", "194.71", "195.20", "192.61", "1"],
                        ["2026-09-28", "195.24", "194.53", "196.15", "192.68", "1"],
                    ]
                }
            }
        }
        rows = parse_tencent_us_daily(payload, key="usXLK.AM")
        self.assertEqual(rows[0].close, Decimal("194.71"))
        self.assertEqual(rows[-1].date, date(2026, 9, 28))

    def test_parse_fx(self) -> None:
        quote = parse_tencent_fx_quote(
            'v_whUSDCNY="310~美元人民币~USDCNY~6.7053~0~20260929125514~";',
            symbol="whUSDCNY",
        )
        self.assertEqual(quote.current, Decimal("6.7053"))

        payload = {
            "data": {
                "whUSDCNY": {
                    "day": [
                        ["2026-09-24", "6.7135", "6.7114", "6.7190", "6.7110", "0"]
                    ]
                }
            }
        }
        rows = parse_tencent_fx_daily(payload, symbol="whUSDCNY")
        self.assertEqual(rows[0].close, Decimal("6.7114"))

    def test_r3_bridge_with_etf_proxy_is_medium_quality(self) -> None:
        row = resolve_r3_qdii_index_bridge(
            fund_code="161128",
            official_nav=Decimal("6.9792"),
            official_nav_date=date(2026, 9, 24),
            proxy_anchor_close=Decimal("194.71"),
            proxy_latest_close=Decimal("194.53"),
            proxy_latest_date=date(2026, 9, 28),
            proxy_id="XLK.AM",
            fx_anchor=Decimal("6.7114"),
            fx_current=Decimal("6.7053"),
            as_of=datetime(2026, 9, 29, 12, 55, tzinfo=TZ),
            exposure_ratio=Decimal("0.95"),
            proxy_exactness="ETF_PROXY",
        )
        self.assertEqual(row.estimated_nav_status, "AVAILABLE")
        self.assertEqual(row.estimated_nav_quality, "MEDIUM")
        self.assertIsNotNone(row.estimated_nav)
        self.assertLess(row.estimated_nav, Decimal("6.9792"))


if __name__ == "__main__":
    unittest.main()
