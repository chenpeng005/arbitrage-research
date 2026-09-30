from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.csi_component_proxy import CsiComponentWeightSet, CsiWeightedConstituent
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

    @patch("runtime.lof.r1_pipeline.fetch_index_quotes_for_mappings")
    def test_batch_resolves_and_deduplicates_proxy_quote(self, fetch_mock) -> None:
        fetch_mock.return_value = {
            ("sh000300", "SH000300"): IndexQuote(
            symbol="sh000300",
            code="000300",
            name="沪深300",
            current=Decimal("4342.07"),
            previous_close=Decimal("4340.76"),
            quote_time=self.now,
            source="TENCENT_QUOTE",
        )
        }

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

    @patch("runtime.lof.r1_pipeline.fetch_index_quotes_for_mappings")
    def test_missing_f10_mapping_still_resolves_at_medium_quality(
        self,
        fetch_mock,
    ) -> None:
        fetch_mock.return_value = {
            ("sh000300", "SH000300"): IndexQuote(
            symbol="sh000300",
            code="000300",
            name="沪深300",
            current=Decimal("4342.07"),
            previous_close=Decimal("4340.76"),
            quote_time=self.now,
            source="TENCENT_QUOTE",
        )
        }
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


    @patch("runtime.lof.r1_pipeline.reconstruct_csi_component_quotes")
    @patch("runtime.lof.r1_pipeline.fetch_index_quotes_for_mappings")
    def test_component_weight_proxy_is_used_for_csi_index(
        self,
        fetch_mock,
        reconstruct_mock,
    ) -> None:
        csi_proxy = IndexProxyMapping(
            tracking_target_name="CS智汽车",
            index_code="930721",
            index_name="CS智汽车",
            quote_id="2.930721",
            market_num="2",
            tencent_symbol=None,
            xueqiu_symbol="CSI930721",
            source="DIRECT_TRACKING_INDEX_CODE",
            status="RESOLVED",
        )
        weight_set = CsiComponentWeightSet(
            index_code="930721",
            index_name="CS智汽车",
            weight_date=date(2026, 9, 14),
            constituent_date=date(2026, 9, 29),
            constituents=(
                CsiWeightedConstituent(
                    code="000001",
                    symbol="sz000001",
                    weight=Decimal("100"),
                ),
            ),
            total_weight=Decimal("100"),
            weight_url="w",
            constituent_url="c",
            fetched_at=self.now,
        )
        reconstruct_mock.return_value = {
            "930721": IndexQuote(
                symbol="CSI_WEIGHT_930721",
                code="930721",
                name="CS智汽车",
                current=Decimal("1.02"),
                previous_close=Decimal("1"),
                quote_time=self.now,
                source="CSI_COMPONENT_WEIGHT_PROXY",
            )
        }
        fetch_mock.return_value = {}

        rows = resolve_r1_batch(
            fund_codes=["163407"],
            official_navs={"163407": self.nav},
            mapping_candidates={"163407": self.mapping},
            proxy_mappings={"163407": csi_proxy},
            component_weight_sets={"930721": weight_set},
            expected_anchor_date=date(2026, 9, 28),
            as_of=self.now,
        )

        self.assertEqual(rows[0].estimated_nav_status, "AVAILABLE")
        self.assertEqual(
            rows[0].resolver_method,
            "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
        )
        self.assertEqual(rows[0].estimated_nav_quality, "MEDIUM")
        reconstruct_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
