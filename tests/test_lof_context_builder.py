from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.classification import FundTypeRecord
from runtime.lof.context_builder import build_estimated_nav_context
from runtime.lof.mapping import ResolverMappingCandidate
from runtime.lof.tracking_index import TrackingIndexRecord
from runtime.lof.universe import LofIdentity


TZ = ZoneInfo("Asia/Shanghai")


class LofEstimatedNavContextBuilderTest(unittest.TestCase):
    @patch("runtime.lof.context_builder.fetch_active_tracking_index_map")
    def test_direct_index_code_builds_r1_without_f10(self, tracking_mock) -> None:
        universe = [
            LofIdentity(
                code="163407",
                name="兴全沪深300指数(LOF)A",
                exchange="SZSE",
            )
        ]
        types = {
            ("SZSE", "163407"): FundTypeRecord(
                code="163407",
                name_raw="兴全沪深300指数(LOF)A",
                fund_type_raw="指数型-股票",
                lof_type="EQUITY",
                source="TEST",
            )
        }
        tracking_mock.return_value = {
            "163407": TrackingIndexRecord(
                fund_code="163407",
                fund_name="兴全沪深300指数(LOF)A",
                tracking_index_code="000300",
                tracking_index_name="沪深300",
                market_bucket="zs:lof",
                source="TEST",
                fetched_at=datetime(2026, 9, 29, tzinfo=TZ),
            )
        }

        result = build_estimated_nav_context(
            universe=universe,
            type_records=types,
            previous_trading_day=date(2026, 9, 28),
            qdii_proxy_registry={},
            commodity_proxy_registry={},
        )

        self.assertEqual(result.r1_resolved_count, 1)
        self.assertEqual(result.r1_unresolved_count, 0)
        proxy = result.context.r1_proxy_mappings["163407"]
        self.assertEqual(proxy.index_code, "000300")
        self.assertEqual(proxy.tencent_symbol, "sh000300")

        mapping = result.context.mapping_candidates["163407"]
        self.assertEqual(mapping.tracking_target_name, "沪深300")
        self.assertIsNone(mapping.exposure_ratio_candidate)

    @patch("runtime.lof.context_builder.fetch_active_tracking_index_map")
    def test_f10_enriches_exposure_but_does_not_replace_direct_code(
        self,
        tracking_mock,
    ) -> None:
        universe = [
            LofIdentity(
                code="163407",
                name="兴全沪深300指数(LOF)A",
                exchange="SZSE",
            )
        ]
        types = {
            ("SZSE", "163407"): FundTypeRecord(
                code="163407",
                name_raw="兴全沪深300指数(LOF)A",
                fund_type_raw="指数型-股票",
                lof_type="EQUITY",
                source="TEST",
            )
        }
        tracking_mock.return_value = {
            "163407": TrackingIndexRecord(
                fund_code="163407",
                fund_name="兴全沪深300指数(LOF)A",
                tracking_index_code="000300",
                tracking_index_name="沪深300",
                market_bucket="zs:lof",
                source="DIRECT",
                fetched_at=datetime(2026, 9, 29, tzinfo=TZ),
            )
        }
        f10 = {
            "163407": ResolverMappingCandidate(
                fund_code="163407",
                tracking_target_name="沪深300指数",
                benchmark_text="沪深300指数*95%+存款*5%",
                exposure_ratio_candidate=__import__("decimal").Decimal("0.95"),
                source="F10",
            )
        }

        result = build_estimated_nav_context(
            universe=universe,
            type_records=types,
            previous_trading_day=date(2026, 9, 28),
            f10_mapping_candidates=f10,
            qdii_proxy_registry={},
            commodity_proxy_registry={},
        )

        self.assertEqual(
            result.context.mapping_candidates["163407"].exposure_ratio_candidate,
            __import__("decimal").Decimal("0.95"),
        )
        self.assertEqual(
            result.context.r1_proxy_mappings["163407"].index_code,
            "000300",
        )


if __name__ == "__main__":
    unittest.main()
