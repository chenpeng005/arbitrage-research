import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.r2_asset_allocation import AssetAllocationSnapshot
from runtime.lof.r2_fund_events import DistributionSchedule
from runtime.lof.r2a_holdings import Holding, HoldingsSnapshot
from runtime.lof.r2a_shadow import LiveQuote
from runtime.lof.r2b2_cash_shadow import PROFILES, calculate_shadow_row


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _holdings(now, code="160916", total="0.7373"):
    first = Decimal(total) * Decimal("0.55")
    second = Decimal(total) - first
    rows = (
        Holding("A", "sh600001", "600001", "A", first),
        Holding("A", "sz000002", "000002", "B", second),
    )
    return HoldingsSnapshot(
        fund_code=code,
        as_of_date=date(2026, 6, 30),
        first_seen_at=now,
        fetched_at=now,
        holdings=rows,
        total_weight=Decimal(total),
        identity="test",
    )


def _allocation(
    now,
    code="160916",
    stock="0.7378",
    bond="0.0035",
    cash="0.2603",
):
    return AssetAllocationSnapshot(
        fund_code=code,
        as_of_date=date(2026, 6, 30),
        fetched_at=now,
        stock_weight=Decimal(stock),
        bond_weight=Decimal(bond),
        cash_weight=Decimal(cash),
    )


def _distribution(now, code="160916"):
    return DistributionSchedule(
        fund_code=code,
        fetched_at=now,
        events=(),
    )


def _snapshot(code="160916", *, lag="T-1", quote_status="FRESH"):
    return {
        "snapshot_id": "main-1",
        "rows": [
            {
                "code": code,
                "name": PROFILES[code]["name"],
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
    def test_registry_contains_three_economic_candidates(self):
        self.assertEqual(set(PROFILES), {"160916", "164403", "501077"})

    def test_fresh_inputs_produce_shadow_only_estimate(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            fund_code="160916",
            profile=PROFILES["160916"],
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

    def test_501077_low_stock_high_cash_structure_is_allowed(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            fund_code="501077",
            profile=PROFILES["501077"],
            main_snapshot=_snapshot("501077"),
            holdings=_holdings(now, "501077", "0.4236"),
            allocation=_allocation(
                now,
                "501077",
                stock="0.4238",
                bond="0",
                cash="0.5643",
            ),
            distribution=_distribution(now, "501077"),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertFalse(row["eligible_for_main"])

    def test_non_t1_nav_fails_closed(self):
        now = datetime(2026, 10, 2, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            fund_code="160916",
            profile=PROFILES["160916"],
            main_snapshot=_snapshot(lag="T-2"),
            holdings=_holdings(now),
            allocation=_allocation(now),
            distribution=_distribution(now),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertIn("OFFICIAL_NAV_NOT_T1", row["error"])

    def test_stale_market_quote_blocks_premium(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            fund_code="160916",
            profile=PROFILES["160916"],
            main_snapshot=_snapshot(quote_status="STALE"),
            holdings=_holdings(now),
            allocation=_allocation(now),
            distribution=_distribution(now),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertIsNone(row["shadow_premium_rate"])

    def test_non_cash_heavy_allocation_fails_closed(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        bad = _allocation(
            now,
            stock="0.60",
            bond="0.20",
            cash="0.20",
        )
        row = calculate_shadow_row(
            fund_code="160916",
            profile=PROFILES["160916"],
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
