from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.estimated_nav_lane import (
    EstimatedNavContext,
    resolve_estimated_nav_lane,
)
from runtime.lof.index_proxy import IndexProxyMapping
from runtime.lof.mapping import ResolverMappingCandidate
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.qdii_proxy_registry import QdiiProxyEntry
from runtime.lof.resolver import EstimatedNavResult
from runtime.lof.resolver_classification import ResolverClassDecision


TZ = ZoneInfo("Asia/Shanghai")


class EstimatedNavLaneTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 13, 0, tzinfo=TZ)
        self.navs = [
            OfficialNavRecord(
                code="163407",
                exchange="SZSE",
                nav=Decimal("2.6"),
                nav_date=date(2026, 9, 28),
                fetched_at=self.now,
                source="SZSE_OFFICIAL",
            ),
            OfficialNavRecord(
                code="161128",
                exchange="SZSE",
                nav=Decimal("6.9792"),
                nav_date=date(2026, 9, 24),
                fetched_at=self.now,
                source="SZSE_OFFICIAL",
            ),
            OfficialNavRecord(
                code="000001",
                exchange="SZSE",
                nav=Decimal("1"),
                nav_date=date(2026, 9, 28),
                fetched_at=self.now,
                source="SZSE_OFFICIAL",
            ),
        ]

    @patch("runtime.lof.estimated_nav_lane.resolve_r3_one")
    @patch("runtime.lof.estimated_nav_lane.resolve_r1_batch")
    def test_dispatches_r1_and_r3_without_hiding_unimplemented(
        self,
        r1_mock,
        r3_mock,
    ) -> None:
        r1_mock.return_value = [
            EstimatedNavResult(
                fund_code="163407",
                estimated_nav=Decimal("2.61"),
                estimated_nav_time=self.now,
                estimated_nav_status="AVAILABLE",
                estimated_nav_quality="HIGH",
                resolver_class="R1_DOMESTIC_INDEX",
                resolver_method="INDEX_PROXY_PREV_CLOSE",
                proxy_id="000300",
                proxy_time=self.now,
                proxy_return=Decimal("0.004"),
                fx_return=None,
                exposure_ratio_used=Decimal("0.95"),
                tracking_adjustment_used=Decimal("1"),
            )
        ]
        r3_mock.return_value = EstimatedNavResult(
            fund_code="161128",
            estimated_nav=Decimal("6.96"),
            estimated_nav_time=self.now,
            estimated_nav_status="AVAILABLE",
            estimated_nav_quality="MEDIUM",
            resolver_class="R3_QDII_INDEX",
            resolver_method="MULTIDAY_PROXY_FX_BRIDGE",
            proxy_id="usXLK",
            proxy_time=None,
            proxy_return=Decimal("-0.001"),
            fx_return=Decimal("-0.001"),
            exposure_ratio_used=Decimal("0.95"),
            tracking_adjustment_used=Decimal("1"),
        )

        context = EstimatedNavContext(
            resolver_classes={
                "163407": ResolverClassDecision(
                    "163407", "R1_DOMESTIC_INDEX", "domestic_index"
                ),
                "161128": ResolverClassDecision(
                    "161128", "R3_QDII_INDEX", "qdii_index"
                ),
                "000001": ResolverClassDecision(
                    "000001", "R2_DOMESTIC_OTHER", "domestic_non_index"
                ),
            },
            mapping_candidates={
                "163407": ResolverMappingCandidate(
                    "163407", "沪深300指数", None, Decimal("0.95"), "TEST"
                ),
                "161128": ResolverMappingCandidate(
                    "161128", "标普500信息科技指数", None, Decimal("0.95"), "TEST"
                ),
            },
            r1_proxy_mappings={
                "163407": IndexProxyMapping(
                    tracking_target_name="沪深300指数",
                    index_code="000300",
                    index_name="沪深300",
                    quote_id="1.000300",
                    market_num="1",
                    tencent_symbol="sh000300",
                    xueqiu_symbol="SH000300",
                    source="TEST",
                    status="RESOLVED",
                )
            },
            qdii_proxy_registry={
                "161128": QdiiProxyEntry(
                    fund_code="161128",
                    tracking_target="标普500信息科技指数",
                    proxy_type="ETF_HIGH_CORR",
                    proxy_symbol="usXLK",
                    history_symbol="XLK.AM",
                    currency="USD",
                    quality="MEDIUM",
                )
            },
            previous_trading_day=date(2026, 9, 28),
        )

        rows = resolve_estimated_nav_lane(
            official_navs=self.navs,
            context=context,
            as_of=self.now,
        )
        by_code = {x.fund_code: x for x in rows}
        self.assertEqual(by_code["163407"].estimated_nav_status, "AVAILABLE")
        self.assertEqual(by_code["161128"].estimated_nav_quality, "MEDIUM")
        self.assertEqual(by_code["000001"].estimated_nav_status, "UNAVAILABLE")
        self.assertEqual(
            by_code["000001"].error,
            "RESOLVER_CLASS_NOT_IMPLEMENTED",
        )


if __name__ == "__main__":
    unittest.main()
