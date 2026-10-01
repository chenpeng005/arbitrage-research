from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.foreign_quote import IndexQuote
from runtime.lof.fx import FxDailyClose, FxQuote
from runtime.lof.hk_history import HkDailyClose
from runtime.lof.mapping import ResolverMappingCandidate
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.qdii_proxy_registry import QdiiProxyEntry
from runtime.lof.r3_pipeline import resolve_r3_one

TZ=ZoneInfo("Asia/Shanghai")

class R3PipelineHkAuditTest(unittest.TestCase):
    @patch("runtime.lof.r3_pipeline.fetch_tencent_foreign_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_hk_daily")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_daily")
    def test_registry_exposure_and_stale_quote_are_propagated(
        self,fx_daily,hk_fx,hk_daily,hk_quote
    ):
        now=datetime(2026,10,1,9,30,tzinfo=TZ)
        nav=OfficialNavRecord(
            code="160924",exchange="SZSE",nav=Decimal("1"),
            nav_date=date(2026,9,30),fetched_at=now,source="TEST"
        )
        mapping=ResolverMappingCandidate(
            fund_code="160924",tracking_target_name="恒生指数",
            benchmark_text=None,exposure_ratio_candidate=None,source="TEST"
        )
        proxy=QdiiProxyEntry(
            fund_code="160924",tracking_target="恒生指数",
            proxy_type="DIRECT_INDEX",proxy_symbol="hkHSI",
            history_symbol="hkHSI",currency="HKD",quality="HIGH",
            exposure_ratio=Decimal("1.0"),
        )
        fx_daily.return_value=[FxDailyClose(date(2026,9,30),Decimal("0.85"))]
        hk_fx.return_value=FxQuote(
            symbol="whHKDCNY",current=Decimal("0.851"),
            quote_time=now-timedelta(seconds=10),source="TEST"
        )
        hk_daily.return_value=[HkDailyClose(date(2026,9,30),Decimal("24000"))]
        hk_quote.return_value=IndexQuote(
            symbol="hkHSI",code="HSI",name="恒生指数",
            current=Decimal("24240"),previous_close=Decimal("24000"),
            quote_time=now-timedelta(seconds=600),source="TEST"
        )
        row=resolve_r3_one(nav=nav,mapping=mapping,proxy=proxy,as_of=now)
        self.assertEqual(row.estimated_nav_status,"STALE")
        self.assertEqual(row.estimated_nav_quality,"HIGH")
        self.assertEqual(row.exposure_ratio_used,Decimal("1.0"))
        self.assertEqual(row.resolver_method,"HK_LIVE_INDEX_FX_BRIDGE")

if __name__ == "__main__":
    unittest.main()
