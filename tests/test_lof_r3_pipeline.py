from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.foreign_quote import IndexQuote
from runtime.lof.fx import FxDailyClose, FxQuote
from runtime.lof.hk_history import HkDailyClose
from runtime.lof.mapping import ResolverMappingCandidate
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.qdii_proxy_registry import QdiiProxyEntry
from runtime.lof.r3_pipeline import resolve_r3_one
from runtime.lof.us_history import DailyClose


TZ = ZoneInfo("Asia/Shanghai")


class LofR3PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 13, 0, tzinfo=TZ)

    @patch("runtime.lof.r3_pipeline.fetch_tencent_foreign_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_hk_daily")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_daily")
    def test_hk_direct_index_can_be_high_quality(
        self,
        fx_daily_mock,
        fx_quote_mock,
        hk_daily_mock,
        foreign_quote_mock,
    ) -> None:
        nav = OfficialNavRecord(
            code="160924",
            exchange="SZSE",
            nav=Decimal("0.9735"),
            nav_date=date(2026, 9, 28),
            fetched_at=self.now,
            source="SZSE_OFFICIAL",
        )
        mapping = ResolverMappingCandidate(
            fund_code="160924",
            tracking_target_name="恒生指数",
            benchmark_text="恒生指数收益率*95%+存款*5%",
            exposure_ratio_candidate=Decimal("0.95"),
            source="EASTMONEY_F10",
        )
        proxy = QdiiProxyEntry(
            fund_code="160924",
            tracking_target="恒生指数",
            proxy_type="DIRECT_INDEX",
            proxy_symbol="hkHSI",
            history_symbol="hkHSI",
            currency="HKD",
            quality="HIGH",
        )
        fx_daily_mock.return_value = [
            FxDailyClose(date(2026, 9, 28), Decimal("0.8553"))
        ]
        fx_quote_mock.return_value = FxQuote(
            symbol="whHKDCNY",
            current=Decimal("0.8544"),
            quote_time=self.now,
            source="TENCENT_FX",
        )
        hk_daily_mock.return_value = [
            HkDailyClose(date(2026, 9, 28), Decimal("24642.510"))
        ]
        foreign_quote_mock.return_value = IndexQuote(
            symbol="hkHSI",
            code="HSI",
            name="恒生指数",
            current=Decimal("24486.520"),
            previous_close=Decimal("24642.510"),
            quote_time=self.now,
            source="TENCENT_FOREIGN_QUOTE",
        )

        row = resolve_r3_one(
            nav=nav,
            mapping=mapping,
            proxy=proxy,
            as_of=self.now,
        )
        self.assertEqual(row.estimated_nav_status, "AVAILABLE")
        self.assertEqual(row.estimated_nav_quality, "HIGH")

    @patch("runtime.lof.r3_pipeline.fetch_tencent_us_daily")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_quote")
    @patch("runtime.lof.r3_pipeline.fetch_tencent_fx_daily")
    def test_us_etf_proxy_is_medium_quality(
        self,
        fx_daily_mock,
        fx_quote_mock,
        us_daily_mock,
    ) -> None:
        nav = OfficialNavRecord(
            code="161128",
            exchange="SZSE",
            nav=Decimal("6.9792"),
            nav_date=date(2026, 9, 24),
            fetched_at=self.now,
            source="SZSE_OFFICIAL",
        )
        mapping = ResolverMappingCandidate(
            fund_code="161128",
            tracking_target_name="标普500信息科技指数",
            benchmark_text="标普500信息科技指数收益率*95%+存款*5%",
            exposure_ratio_candidate=Decimal("0.95"),
            source="EASTMONEY_F10",
        )
        proxy = QdiiProxyEntry(
            fund_code="161128",
            tracking_target="标普500信息科技指数",
            proxy_type="ETF_HIGH_CORR",
            proxy_symbol="usXLK",
            history_symbol="XLK.AM",
            currency="USD",
            quality="MEDIUM",
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
        us_daily_mock.return_value = [
            DailyClose(date(2026, 9, 24), Decimal("194.71")),
            DailyClose(date(2026, 9, 28), Decimal("194.53")),
        ]

        row = resolve_r3_one(
            nav=nav,
            mapping=mapping,
            proxy=proxy,
            as_of=self.now,
        )
        self.assertEqual(row.estimated_nav_status, "AVAILABLE")
        self.assertEqual(row.estimated_nav_quality, "MEDIUM")

    def test_unresolved_proxy_fails_closed(self) -> None:
        nav = OfficialNavRecord(
            code="161124",
            exchange="SZSE",
            nav=Decimal("1"),
            nav_date=date(2026, 9, 28),
            fetched_at=self.now,
            source="SZSE_OFFICIAL",
        )
        mapping = ResolverMappingCandidate(
            fund_code="161124",
            tracking_target_name="恒生综合小型股指数",
            benchmark_text=None,
            exposure_ratio_candidate=Decimal("0.95"),
            source="EASTMONEY_F10",
        )
        proxy = QdiiProxyEntry(
            fund_code="161124",
            tracking_target="恒生综合小型股指数",
            proxy_type="UNRESOLVED",
            proxy_symbol=None,
            history_symbol=None,
            currency="HKD",
            quality="UNKNOWN",
        )
        row = resolve_r3_one(
            nav=nav,
            mapping=mapping,
            proxy=proxy,
            as_of=self.now,
        )
        self.assertEqual(row.estimated_nav_status, "UNAVAILABLE")
        self.assertEqual(row.error, "UNRESOLVED_PROXY")


if __name__ == "__main__":
    unittest.main()
