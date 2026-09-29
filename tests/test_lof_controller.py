from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.classification import FundTypeRecord
from runtime.lof.controller import collect_market_snapshot
from runtime.lof.estimated_nav_lane import EstimatedNavContext
from runtime.lof.nav import OfficialNavRecord
from runtime.lof.quote import QuoteRecord
from runtime.lof.resolver import EstimatedNavResult
from runtime.lof.state import FundTradeStateRecord
from runtime.lof.universe import LofIdentity


TZ = ZoneInfo("Asia/Shanghai")


class LofCollectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 11, 30, tzinfo=TZ)
        self.universe = [
            LofIdentity(
                code="501225",
                name="全球芯片LOF",
                exchange="SSE",
                source="SSE_OFFICIAL",
            )
        ]

    @patch("runtime.lof.controller.fetch_fund_type_map")
    @patch("runtime.lof.controller.fetch_all_trade_states")
    @patch("runtime.lof.controller.fetch_all_official_nav")
    @patch("runtime.lof.controller.fetch_quotes")
    @patch("runtime.lof.controller.fetch_all_lof_universe")
    def test_collector_pass(
        self,
        universe_mock,
        quote_mock,
        nav_mock,
        state_mock,
        type_mock,
    ) -> None:
        universe_mock.return_value = self.universe
        quote_mock.return_value = [
            QuoteRecord(
                code="501225",
                exchange="SSE",
                name="全球芯片LOF",
                price=Decimal("3.85"),
                quote_time=self.now,
                pct_change=Decimal("0.1"),
                volume=Decimal("1000"),
                amount=Decimal("3850000"),
                source="TENCENT_QUOTE",
            )
        ]
        nav_mock.return_value = [
            OfficialNavRecord(
                code="501225",
                exchange="SSE",
                nav=Decimal("3.20"),
                nav_date=date(2026, 9, 28),
                fetched_at=self.now,
                source="SSE_OFFICIAL",
            )
        ]
        state_mock.return_value = [
            FundTradeStateRecord(
                code="501225",
                subscription_status="SUSPENDED",
                subscription_status_raw="暂停申购",
                redemption_status="OPEN",
                redemption_status_raw="开放赎回",
                daily_subscription_limit=Decimal("100"),
                minimum_subscription_amount=Decimal("10"),
                limit_scope="UNKNOWN",
                subscription_confirmation_days=2,
                subscription_to_sell_days=None,
                subscription_fee_schedule=(),
                redemption_fee_schedule=(),
                fee_source="EASTMONEY_CHANNEL_REFERENCE",
                state_source="EASTMONEY_FUND_MOBILE",
                fetched_at=self.now,
            )
        ]
        type_mock.return_value = {
            "501225": FundTypeRecord(
                code="501225",
                name_raw="景顺长城全球半导体芯片股票A(QDII-LOF)(人民币)",
                fund_type_raw="QDII-普通股票",
                lof_type="QDII_EQUITY",
                source="EASTMONEY_FUND_CODE",
            )
        }

        snapshot = collect_market_snapshot(
            generated_at=self.now,
            market_cutoff=self.now,
            max_quote_age_seconds=30,
            snapshot_id="collector-pass",
        )

        self.assertEqual(snapshot["collector_status"], "PASS")
        self.assertEqual(snapshot["lane_errors"], {})
        self.assertEqual(snapshot["universe_count"], 1)
        row = snapshot["rows"][0]
        self.assertEqual(row["lof_type"], "QDII_EQUITY")
        self.assertEqual(row["subscription_status"], "SUSPENDED")
        self.assertEqual(row["subscription_confirmation_days"], 2)
        self.assertIsNone(row["subscription_to_sell_days"])


    @patch("runtime.lof.controller.resolve_estimated_nav_lane")
    @patch("runtime.lof.controller.fetch_fund_type_map")
    @patch("runtime.lof.controller.fetch_all_trade_states")
    @patch("runtime.lof.controller.fetch_all_official_nav")
    @patch("runtime.lof.controller.fetch_quotes")
    @patch("runtime.lof.controller.fetch_all_lof_universe")
    def test_collector_projects_estimated_nav_when_context_available(
        self,
        universe_mock,
        quote_mock,
        nav_mock,
        state_mock,
        type_mock,
        estimated_mock,
    ) -> None:
        universe_mock.return_value = self.universe
        quote_mock.return_value = [
            QuoteRecord(
                code="501225",
                exchange="SSE",
                name="全球芯片LOF",
                price=Decimal("3.85"),
                quote_time=self.now,
                pct_change=None,
                volume=None,
                amount=None,
                source="TENCENT_QUOTE",
            )
        ]
        nav_mock.return_value = [
            OfficialNavRecord(
                code="501225",
                exchange="SSE",
                nav=Decimal("3.20"),
                nav_date=date(2026, 9, 28),
                fetched_at=self.now,
                source="SSE_OFFICIAL",
            )
        ]
        state_mock.return_value = []
        type_mock.return_value = {}
        estimated_mock.return_value = [
            EstimatedNavResult(
                fund_code="501225",
                estimated_nav=Decimal("3.50"),
                estimated_nav_time=self.now,
                estimated_nav_status="AVAILABLE",
                estimated_nav_quality="MEDIUM",
                resolver_class="R3_QDII_INDEX",
                resolver_method="TEST",
                proxy_id="TEST",
                proxy_time=self.now,
                proxy_return=Decimal("0"),
                fx_return=Decimal("0"),
                exposure_ratio_used=Decimal("1"),
                tracking_adjustment_used=Decimal("1"),
            )
        ]

        context = EstimatedNavContext(
            resolver_classes={},
            mapping_candidates={},
            r1_proxy_mappings={},
            qdii_proxy_registry={},
            previous_trading_day=date(2026, 9, 28),
        )

        snapshot = collect_market_snapshot(
            generated_at=self.now,
            market_cutoff=self.now,
            max_quote_age_seconds=30,
            estimated_nav_context=context,
            snapshot_id="collector-estimated",
        )

        row = snapshot["rows"][0]
        self.assertEqual(row["estimated_nav"], Decimal("3.50"))
        self.assertEqual(row["estimated_nav_status"], "AVAILABLE")
        self.assertEqual(row["estimated_nav_quality"], "MEDIUM")
        self.assertIsNotNone(row["estimated_premium_rate"])

    @patch("runtime.lof.controller.fetch_fund_type_map", return_value={})
    @patch(
        "runtime.lof.controller.fetch_all_trade_states",
        side_effect=RuntimeError("state source down"),
    )
    @patch("runtime.lof.controller.fetch_all_official_nav", return_value=[])
    @patch("runtime.lof.controller.fetch_quotes", return_value=[])
    @patch("runtime.lof.controller.fetch_all_lof_universe")
    def test_collector_degrades_lane_without_deleting_universe(
        self,
        universe_mock,
        quote_mock,
        nav_mock,
        state_mock,
        type_mock,
    ) -> None:
        universe_mock.return_value = self.universe

        snapshot = collect_market_snapshot(
            generated_at=self.now,
            market_cutoff=self.now,
            max_quote_age_seconds=30,
            snapshot_id="collector-degraded",
        )

        self.assertEqual(snapshot["collector_status"], "DEGRADED")
        self.assertIn("trade_state", snapshot["lane_errors"])
        self.assertEqual(len(snapshot["rows"]), 1)
        self.assertEqual(
            snapshot["rows"][0]["subscription_status"],
            "UNKNOWN",
        )

    @patch(
        "runtime.lof.controller.fetch_all_lof_universe",
        side_effect=RuntimeError("exchange unavailable"),
    )
    def test_universe_failure_fails_whole_snapshot(self, universe_mock) -> None:
        with self.assertRaises(RuntimeError):
            collect_market_snapshot(
                generated_at=self.now,
                market_cutoff=self.now,
                max_quote_age_seconds=30,
            )


if __name__ == "__main__":
    unittest.main()
