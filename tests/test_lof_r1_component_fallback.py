from __future__ import annotations
import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo
from runtime.lof.csi_component_proxy import CsiComponentWeightSet, CsiWeightedConstituent
from runtime.lof.index_proxy import IndexProxyMapping
from runtime.lof.index_quote import IndexQuote
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.r1_pipeline import resolve_r1_batch

TZ = ZoneInfo("Asia/Shanghai")

class LofR1ComponentFallbackTest(unittest.TestCase):
    @patch("runtime.lof.r1_pipeline.reconstruct_csi_component_quotes")
    @patch("runtime.lof.r1_pipeline.fetch_index_quotes_for_mappings")
    def test_component_proxy_preserves_current_r1_semantics(self, standard_mock, component_mock):
        standard_mock.return_value = {}
        component_mock.return_value = {
            "930721": IndexQuote(
                symbol="CSI_WEIGHT_930721", code="930721", name="CS智汽车",
                current=Decimal("1.01"), previous_close=Decimal("1"),
                quote_time=datetime(2026, 9, 30, 10, 0, tzinfo=TZ),
                source="CSI_COMPONENT_WEIGHT_PROXY", error=None,
            )
        }
        proxy = IndexProxyMapping(
            tracking_target_name="CS智汽车", index_code="930721",
            index_name="CS智汽车", quote_id="2.930721", market_num="2",
            tencent_symbol=None, xueqiu_symbol="CSI930721",
            source="DIRECT_TRACKING_INDEX_CODE", status="RESOLVED", error=None,
        )
        weights = CsiComponentWeightSet(
            index_code="930721", index_name="CS智汽车",
            weight_date=date(2026, 8, 31), constituent_date=date(2026, 9, 29),
            constituents=(CsiWeightedConstituent("000001", "sz000001", Decimal("100")),),
            total_weight=Decimal("100"), weight_url="w", constituent_url="c",
            fetched_at=datetime(2026, 9, 30, 8, 30, tzinfo=TZ), error=None,
        )
        nav = OfficialNavRecord(
            code="161033", exchange="SZSE", nav=Decimal("1"),
            nav_date=date(2026, 9, 29),
            fetched_at=datetime(2026, 9, 30, 8, 30, tzinfo=TZ), source="TEST",
        )
        result = resolve_r1_batch(
            fund_codes=["161033"], official_navs={"161033": nav},
            mapping_candidates={}, proxy_mappings={"161033": proxy},
            component_weight_sets={"930721": weights},
            expected_anchor_date=date(2026, 9, 29),
            as_of=datetime(2026, 9, 30, 10, 0, 10, tzinfo=TZ),
        )[0]
        self.assertEqual(result.estimated_nav_status, "AVAILABLE")
        self.assertEqual(result.resolver_method, "CSI_COMPONENT_WEIGHT_PREV_CLOSE")
        self.assertEqual(result.estimated_nav_quality, "MEDIUM")

if __name__ == "__main__":
    unittest.main()