from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.fx import FxDailyClose, FxQuote
from runtime.lof.futures_overlay import FuturesOverlayQuote
from runtime.lof.mapping import ResolverMappingCandidate
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.qdii_proxy_registry import QdiiProxyEntry
from runtime.lof.r3_pipeline import resolve_r3_one
from runtime.lof.us_history import DailyClose, us_cash_close_time

TZ = ZoneInfo("Asia/Shanghai")

class R3UsTimingTest(unittest.TestCase):
    def test_us_cash_close_time_handles_dst(self):
        self.assertEqual(
            us_cash_close_time(date(2026, 9, 30)),
            datetime(2026, 10, 1, 4, 0, tzinfo=TZ),
        )
        self.assertEqual(
            us_cash_close_time(date(2026, 12, 1)),
            datetime(2026, 12, 2, 5, 0, tzinfo=TZ),
        )

    def _inputs(self, *, futures=True):
        now=datetime(2026,10,1,11,0,tzinfo=TZ)
        nav=OfficialNavRecord(
            code="161125", exchange="SZSE", nav=Decimal("2"),
            nav_date=date(2026,9,29), fetched_at=now, source="TEST"
        )
        mapping=ResolverMappingCandidate(
            fund_code="161125", tracking_target_name="标普500指数",
            benchmark_text=None, exposure_ratio_candidate=Decimal("1"), source="TEST"
        )
        proxy=QdiiProxyEntry(
            fund_code="161125", tracking_target="标普500指数",
            proxy_type="ETF_SAME_INDEX", proxy_symbol="usSPY",
            history_symbol="SPY.AM", currency="USD", quality="MEDIUM",
            futures_overlay_market="103" if futures else None,
            futures_overlay_code="ES00Y" if futures else None,
            futures_overlay_quality="MEDIUM" if futures else None,
        )
        return now,nav,mapping,proxy

    @patch("runtime.lof.r3_pipeline.fetch_eastmoney_global_futures")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_us_daily")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_daily")
    def test_fresh_futures_and_fx_are_available(
        self,fx_daily,fx_quote,us_daily,fut
    ):
        now,nav,mapping,proxy=self._inputs(futures=True)
        fx_daily.return_value=[FxDailyClose(date(2026,9,29),Decimal("6.70"))]
        fx_quote.return_value=FxQuote(
            symbol="whUSDCNY", current=Decimal("6.71"),
            quote_time=now-timedelta(seconds=20), source="TEST"
        )
        us_daily.return_value=[
            DailyClose(date(2026,9,29),Decimal("700")),
            DailyClose(date(2026,9,30),Decimal("705")),
        ]
        fut.return_value=FuturesOverlayQuote(
            code="ES00Y", current=Decimal("7100"),
            previous_settlement=Decimal("7050"), pct_change=None,
            quote_time=now-timedelta(seconds=10), source="TEST", error=None
        )
        row=resolve_r3_one(nav=nav,mapping=mapping,proxy=proxy,as_of=now)
        self.assertEqual(row.estimated_nav_status,"AVAILABLE")
        self.assertEqual(row.resolver_method,"US_FUTURES_FX_BRIDGE")
        self.assertEqual(row.estimated_nav_time,now-timedelta(seconds=20))

    @patch("runtime.lof.r3_pipeline.fetch_eastmoney_global_futures")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_us_daily")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_daily")
    def test_stale_fx_makes_futures_bridge_stale(
        self,fx_daily,fx_quote,us_daily,fut
    ):
        now,nav,mapping,proxy=self._inputs(futures=True)
        fx_daily.return_value=[FxDailyClose(date(2026,9,29),Decimal("6.70"))]
        fx_quote.return_value=FxQuote(
            symbol="whUSDCNY", current=Decimal("6.71"),
            quote_time=now-timedelta(hours=8), source="TEST"
        )
        us_daily.return_value=[
            DailyClose(date(2026,9,29),Decimal("700")),
            DailyClose(date(2026,9,30),Decimal("705")),
        ]
        fut.return_value=FuturesOverlayQuote(
            code="ES00Y", current=Decimal("7100"),
            previous_settlement=Decimal("7050"), pct_change=None,
            quote_time=now-timedelta(seconds=10), source="TEST", error=None
        )
        row=resolve_r3_one(nav=nav,mapping=mapping,proxy=proxy,as_of=now)
        self.assertEqual(row.estimated_nav_status,"STALE")
        self.assertEqual(row.resolver_method,"US_FUTURES_FX_BRIDGE")
        self.assertEqual(row.estimated_nav_time,now-timedelta(hours=8))

    @patch("runtime.lof.r3_pipeline.fetch_tencent_us_daily")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_daily")
    def test_cash_only_is_cross_session_stale(
        self,fx_daily,fx_quote,us_daily
    ):
        now,nav,mapping,proxy=self._inputs(futures=False)
        fx_daily.return_value=[FxDailyClose(date(2026,9,29),Decimal("6.70"))]
        fx_quote.return_value=FxQuote(
            symbol="whUSDCNY", current=Decimal("6.71"),
            quote_time=now-timedelta(seconds=20), source="TEST"
        )
        us_daily.return_value=[
            DailyClose(date(2026,9,29),Decimal("700")),
            DailyClose(date(2026,9,30),Decimal("705")),
        ]
        row=resolve_r3_one(nav=nav,mapping=mapping,proxy=proxy,as_of=now)
        self.assertEqual(row.estimated_nav_status,"STALE")
        self.assertEqual(row.resolver_method,"US_LAST_CLOSE_FX_BRIDGE")
        self.assertEqual(row.estimated_nav_time,datetime(2026,10,1,4,0,tzinfo=TZ))
        self.assertEqual(row.estimated_nav_quality,"LOW")

if __name__=="__main__":
    unittest.main()
