from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.index_proxy import IndexProxyMapping
from runtime.lof.index_quote import IndexQuote
from runtime.lof.mapping import ResolverMappingCandidate
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.r1_pipeline import resolve_r1_batch


TZ = ZoneInfo("Asia/Shanghai")


class LofR1PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 10, 30, tzinfo=TZ)
        self.nav = OfficialNavRecord(
            code="163407",
            exchange="SZSE",
            nav=Decimal("2.6000"),
            nav_date=date(2026, 9, 28),
            fetched_at=self.now,
            source="SZSE_OFFICIAL",
        )
        self.mapping = ResolverMappingCandidate(
            fund_code="163407",
            tracking_target_name="沪深300指数",
            benchmark_text="沪深300指数*95%+同业存款利率*5%",
            exposure_ratio_candidate=Decimal("0.95"),
            source="EASTMONEY_F10",
        )
        self.proxy = IndexProxyMapping(
            tracking_target_name="沪深300指数",
            index_code="000300",
            index_name="沪深300",
            quote_id="1.000300",
            market_num="1",
            tencent_symbol="sh000300",
            xueqiu_symbol="SH000300",
            source="EASTMONEY_SUGGEST",
            status="RESOLVED",
        )

    @patch("runtime.lof.r1_pipeline.fetch_index_quote_with_fallback")
    def test_batch_resolves_and_deduplicates_proxy_quote(self, fetch_mock) -> None:
        fetch_mock.return_value = IndexQuote(
            symbol="sh000300",
            code="000300",
            name="沪深300",
            current=Decimal("4342.07"),
            previous_close=Decimal("4340.76"),
            quote_time=self.now,
            source="TENCENT_QUOTE",
        )

        second_mapping = ResolverMappingCandidate(
            fund_code="160615",
            tracking_target_name="沪深300指数",
            benchmark_text="沪深300指数收益率*95%+存款*5%",
            exposure_ratio_candidate=Decimal("0.95"),
            source="EASTMONEY_F10",
        )
        second_nav = OfficialNavRecord(
            code="160615",
            exchange="SZSE",
            nav=Decimal("1.9000"),
            nav_date=date(2026, 9, 28),
            fetched_at=self.now,
            source="SZSE_OFFICIAL",
        )

        rows = resolve_r1_batch(
            fund_codes=["163407", "160615"],
            official_navs={"163407": self.nav, "160615": second_nav},
            mapping_candidates={
                "163407": self.mapping,
                "160615": second_mapping,
            },
            proxy_mappings={
                "163407": self.proxy,
                "160615": self.proxy,
            },
            expected_anchor_date=date(2026, 9, 28),
            as_of=self.now,
        )

        self.assertEqual(len(rows), 2)
        self.assertTrue(all(x.estimated_nav_status == "AVAILABLE" for x in rows))
        self.assertEqual(fetch_mock.call_count, 1)

    @patch("runtime.lof.r1_pipeline.fetch_index_quote_with_fallback")
    def test_missing_f10_mapping_still_resolves_at_medium_quality(
        self,
        fetch_mock,
    ) -> None:
        fetch_mock.return_value = IndexQuote(
            symbol="sh000300",
            code="000300",
            name="沪深300",
            current=Decimal("4342.07"),
            previous_close=Decimal("4340.76"),
            quote_time=self.now,
            source="TENCENT_QUOTE",
        )
        rows = resolve_r1_batch(
            fund_codes=["163407"],
            official_navs={"163407": self.nav},
            mapping_candidates={},
            proxy_mappings={"163407": self.proxy},
            expected_anchor_date=date(2026, 9, 28),
            as_of=self.now,
        )
        self.assertEqual(rows[0].estimated_nav_status, "AVAILABLE")
        self.assertEqual(rows[0].estimated_nav_quality, "MEDIUM")
        self.assertEqual(rows[0].exposure_ratio_used, Decimal("1"))


if __name__ == "__main__":
    unittest.main()
