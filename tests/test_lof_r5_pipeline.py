from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.commodity_history import CommodityDailyClose
from runtime.lof.commodity_proxy_registry import CommodityProxyEntry
from runtime.lof.commodity_quote import CommodityLiveQuote
from runtime.lof.fx import FxDailyClose, FxQuote
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.r5_pipeline import resolve_r5_commodity_one


TZ = ZoneInfo("Asia/Shanghai")


class LofR5CommodityPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 13, 15, tzinfo=TZ)

    @patch("runtime.lof.r5_pipeline.fetch_tencent_fx_quote")
    @patch("runtime.lof.r5_pipeline.fetch_tencent_fx_daily")
    @patch("runtime.lof.r5_pipeline.fetch_eastmoney_commodity_quote")
    @patch("runtime.lof.r5_pipeline.fetch_sina_global_futures_daily")
    def test_gold_proxy_bridge(
        self,
        hist_mock,
        quote_mock,
        fx_daily_mock,
        fx_quote_mock,
    ) -> None:
        hist_mock.return_value = [
            CommodityDailyClose(date(2026, 9, 24), Decimal("4310"))
        ]
        quote_mock.return_value = CommodityLiveQuote(
            code="GC00Y",
            current=Decimal("4159.7"),
            previous_settlement=Decimal("4168.4"),
            quote_time=self.now,
            source="EASTMONEY_FUTSSEAPI",
        )
        fx_daily_mock.return_value = [
            FxDailyClose(date(2026, 9, 24), Decimal("6.7114"))
        ]
        fx_quote_mock.return_value = FxQuote(
            symbol="whUSDCNY",
            current=Decimal("6.7053"),
            quote_time=self.now,
            source="TENCENT_FX",
        )
        nav = OfficialNavRecord(
            code="160719",
            exchange="SZSE",
            nav=Decimal("1.8820"),
            nav_date=date(2026, 9, 24),
            fetched_at=self.now,
            source="SZSE_OFFICIAL",
        )
        proxy = CommodityProxyEntry(
            fund_code="160719",
            benchmark="London Gold",
            status="RESOLVED",
            commodity_history_symbol="GC",
            commodity_live_market="101",
            commodity_live_code="GC00Y",
            currency="USD",
            exposure_ratio=Decimal("1"),
            proxy_quality="MEDIUM",
        )
        row = resolve_r5_commodity_one(
            nav=nav,
            proxy=proxy,
            as_of=self.now,
        )
        self.assertEqual(row.estimated_nav_status, "AVAILABLE")
        self.assertEqual(row.estimated_nav_quality, "MEDIUM")
        self.assertLess(row.estimated_nav, nav.nav)

    def test_unresolved_proxy_fails_closed(self) -> None:
        nav = OfficialNavRecord(
            code="160216",
            exchange="SZSE",
            nav=Decimal("1"),
            nav_date=date(2026, 9, 24),
            fetched_at=self.now,
            source="SZSE_OFFICIAL",
        )
        proxy = CommodityProxyEntry(
            fund_code="160216",
            benchmark="broad commodity",
            status="UNRESOLVED",
        )
        row = resolve_r5_commodity_one(
            nav=nav,
            proxy=proxy,
            as_of=self.now,
        )
        self.assertEqual(row.estimated_nav_status, "UNAVAILABLE")
        self.assertEqual(row.error, "UNRESOLVED_COMMODITY_PROXY")


if __name__ == "__main__":
    unittest.main()
