import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.r2_asset_allocation import AssetAllocationSnapshot
from runtime.lof.r2_fund_events import DistributionSchedule
from runtime.lof.r2a_holdings import Holding, HoldingsSnapshot
from runtime.lof.r2a_shadow import LiveQuote
from runtime.lof.r2b2_cash_shadow import calculate_shadow_row


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _holdings(now):
    rows = (
        Holding("A", "sh600001", "600001", "A", Decimal("0.40")),
        Holding("A", "sz000002", "000002", "B", Decimal("0.3373")),
    )
    return HoldingsSnapshot(
        fund_code="160916",
        as_of_date=date(2026, 6, 30),
        first_seen_at=now,
        fetched_at=now,
        holdings=rows,
        total_weight=Decimal("0.7373"),
        identity="test",
    )


def _allocation(now):
    return AssetAllocationSnapshot(
        fund_code="160916",
        as_of_date=date(2026, 6, 30),
        fetched_at=now,
        stock_weight=Decimal("0.7378"),
        bond_weight=Decimal("0.0035"),
        cash_weight=Decimal("0.2603"),
    )


def _distribution(now):
    return DistributionSchedule(
        fund_code="160916",
        fetched_at=now,
        events=(),
    )


def _snapshot(*, lag="T-1", quote_status="FRESH"):
    return {
        "snapshot_id": "main-1",
        "rows": [
            {
                "code": "160916",
                "name": "优选LOF",
                "price": 1.10,
                "quote_status": quote_status,
                "official_nav": 1.00,
                "official_nav_date": "2026-09-29",
                "official_nav_lag_label": lag,
            }
        ],
    }


def _quotes(now):
    return {
        "sh600001": LiveQuote(
            "sh600001",
            Decimal("102"),
            Decimal("100"),
            now,
            None,
        ),
        "sz000002": LiveQuote(
            "sz000002",
            Decimal("99"),
            Decimal("100"),
            now,
            None,
        ),
    }


class R2B2CashShadowTests(unittest.TestCase):
    def test_fresh_inputs_produce_shadow_only_estimate(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(),
            holdings=_holdings(now),
            allocation=_allocation(now),
            distribution=_distribution(now),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertTrue(row["shadow_estimated_nav"] > 1)
        self.assertIsNotNone(row["shadow_premium_rate"])
        self.assertFalse(row["eligible_for_main"])
        self.assertAlmostEqual(row["live_coverage_ratio"], 1.0, places=9)

    def test_non_t1_nav_fails_closed(self):
        now = datetime(2026, 10, 2, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(lag="T-2"),
            holdings=_holdings(now),
            allocation=_allocation(now),
            distribution=_distribution(now),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertIn("OFFICIAL_NAV_NOT_T1", row["error"])
        self.assertIsNone(row["shadow_estimated_nav"])

    def test_stale_market_quote_blocks_premium(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(quote_status="STALE"),
            holdings=_holdings(now),
            allocation=_allocation(now),
            distribution=_distribution(now),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertIsNotNone(row["shadow_estimated_nav"])
        self.assertIsNone(row["shadow_premium_rate"])

    def test_non_cash_heavy_allocation_fails_closed(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        bad = AssetAllocationSnapshot(
            fund_code="160916",
            as_of_date=date(2026, 6, 30),
            fetched_at=now,
            stock_weight=Decimal("0.60"),
            bond_weight=Decimal("0.20"),
            cash_weight=Decimal("0.20"),
        )
        row = calculate_shadow_row(
            main_snapshot=_snapshot(),
            holdings=_holdings(now),
            allocation=bad,
            distribution=_distribution(now),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["error"], "ASSET_ALLOCATION_NOT_CASH_HEAVY")


if __name__ == "__main__":
    unittest.main()
