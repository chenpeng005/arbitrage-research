from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.commodity_proxy_registry import CommodityProxyEntry
from runtime.lof.commodity_quote import CommodityLiveQuote
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.r5_pipeline import resolve_r5_commodity_one

TZ = ZoneInfo("Asia/Shanghai")


class R5PipelineTest(unittest.TestCase):
    def nav(self, nav_date=date(2026, 9, 30)):
        now=datetime(2026,10,1,10,0,tzinfo=TZ)
        return OfficialNavRecord(
            code="161226",
            exchange="SZSE",
            nav=Decimal("1.5"),
            nav_date=nav_date,
            fetched_at=now,
            source="TEST",
        )

    def silver_proxy(self):
        return CommodityProxyEntry(
            fund_code="161226",
            benchmark="SHFE Silver",
            status="RESOLVED",
            commodity_live_market="113",
            commodity_live_code="agm",
            currency="CNY",
            exposure_ratio=Decimal("1"),
            proxy_quality="MEDIUM",
            anchor_mode="PREVIOUS_SETTLEMENT",
        )

    @patch("runtime.lof.r5_pipeline.fetch_eastmoney_commodity_quote")
    def test_domestic_silver_previous_settlement_fast_path(self, qmock):
        now=datetime(2026,10,1,10,0,tzinfo=TZ)
        qmock.return_value=CommodityLiveQuote(
            code="agm",
            current=Decimal("101"),
            previous_settlement=Decimal("100"),
            quote_time=now-timedelta(seconds=10),
            source="TEST",
            error=None,
        )
        r=resolve_r5_commodity_one(
            nav=self.nav(),
            proxy=self.silver_proxy(),
            as_of=now,
            expected_anchor_date=date(2026,9,30),
        )
        self.assertEqual(r.estimated_nav_status,"AVAILABLE")
        self.assertEqual(r.resolver_method,"DOMESTIC_FUTURES_PREV_SETTLEMENT")
        self.assertEqual(r.estimated_nav,Decimal("1.515"))
        self.assertEqual(r.proxy_time,now-timedelta(seconds=10))

    @patch("runtime.lof.r5_pipeline.fetch_eastmoney_commodity_quote")
    def test_domestic_silver_stale_quote_is_stale(self, qmock):
        now=datetime(2026,10,1,10,0,tzinfo=TZ)
        qmock.return_value=CommodityLiveQuote(
            code="agm",
            current=Decimal("101"),
            previous_settlement=Decimal("100"),
            quote_time=now-timedelta(seconds=300),
            source="TEST",
            error=None,
        )
        r=resolve_r5_commodity_one(
            nav=self.nav(),
            proxy=self.silver_proxy(),
            as_of=now,
            expected_anchor_date=date(2026,9,30),
            max_proxy_age_seconds=180,
        )
        self.assertEqual(r.estimated_nav_status,"STALE")
        self.assertEqual(r.estimated_nav_time,now-timedelta(seconds=300))

    @patch("runtime.lof.r5_pipeline.fetch_eastmoney_commodity_quote")
    def test_domestic_silver_requires_t1_nav_alignment(self, qmock):
        now=datetime(2026,10,1,10,0,tzinfo=TZ)
        qmock.return_value=CommodityLiveQuote(
            code="agm",
            current=Decimal("101"),
            previous_settlement=Decimal("100"),
            quote_time=now,
            source="TEST",
            error=None,
        )
        r=resolve_r5_commodity_one(
            nav=self.nav(date(2026,9,29)),
            proxy=self.silver_proxy(),
            as_of=now,
            expected_anchor_date=date(2026,9,30),
        )
        self.assertEqual(r.estimated_nav_status,"UNAVAILABLE")
        self.assertEqual(r.error,"NAV_DATE_NOT_PREVIOUS_TRADING_DAY")


if __name__ == "__main__":
    unittest.main()
