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
    def test_audited_share_alias_fills_missing_tracking_row(
        self,
        tracking_mock,
    ) -> None:
        universe = [
            LofIdentity(code="501005", name="精准医疗LOF", exchange="SSE"),
            LofIdentity(code="501006", name="精准医C", exchange="SSE"),
        ]
        types = {
            ("SSE", code): FundTypeRecord(
                code=code,
                name_raw=name,
                fund_type_raw="指数型-股票",
                lof_type="EQUITY",
                source="TEST",
            )
            for code, name in (
                ("501005", "精准医疗LOF"),
                ("501006", "精准医C"),
            )
        }
        tracking_mock.return_value = {
            "501005": TrackingIndexRecord(
                fund_code="501005",
                fund_name="汇添富中证精准医疗指数(LOF)A",
                tracking_index_code="930719",
                tracking_index_name="CS精准医",
                market_bucket="zs:lof",
                source="TEST",
                fetched_at=datetime(2026, 9, 30, tzinfo=TZ),
            )
        }

        result = build_estimated_nav_context(
            universe=universe,
            type_records=types,
            previous_trading_day=date(2026, 9, 29),
            qdii_proxy_registry={},
            commodity_proxy_registry={},
        )

        alias = result.tracking_index_map["501006"]
        self.assertEqual(alias.tracking_index_code, "930719")
        self.assertIn("R1_AUDITED_SHARE_ALIAS:501005", alias.source)
        self.assertEqual(
            result.context.r1_proxy_mappings["501006"].index_code,
            "930719",
        )

    @patch("runtime.lof.context_builder.fetch_active_tracking_index_map")
    def test_target_etf_override_uses_explicit_proxy_and_exposure(
        self,
        tracking_mock,
    ) -> None:
        universe = [
            LofIdentity(code="501029", name="红利基金LOF", exchange="SSE")
        ]
        types = {
            ("SSE", "501029"): FundTypeRecord(
                code="501029",
                name_raw="红利基金LOF",
                fund_type_raw="指数型-股票",
                lof_type="EQUITY",
                source="TEST",
            )
        }
        tracking_mock.return_value = {
            "501029": TrackingIndexRecord(
                fund_code="501029",
                fund_name="华宝标普中国A股红利机会ETF联接A(LOF)",
                tracking_index_code="CSPSADRP",
                tracking_index_name="标普中国A股红利机会指数",
                market_bucket="zs:lof",
                source="TEST",
                fetched_at=datetime(2026, 9, 30, tzinfo=TZ),
            )
        }

        result = build_estimated_nav_context(
            universe=universe,
            type_records=types,
            previous_trading_day=date(2026, 9, 29),
            qdii_proxy_registry={},
            commodity_proxy_registry={},
        )

        proxy = result.context.r1_proxy_mappings["501029"]
        self.assertEqual(proxy.index_code, "562060")
        self.assertEqual(proxy.tencent_symbol, "sh562060")
        self.assertEqual(proxy.source, "R1_TARGET_ETF_OVERRIDE")
        mapping = result.context.mapping_candidates["501029"]
        self.assertEqual(
            mapping.exposure_ratio_candidate,
            __import__("decimal").Decimal("0.95"),
        )

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
