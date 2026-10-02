import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.r2_asset_allocation import AssetAllocationSnapshot
from runtime.lof.r2_fund_events import DistributionSchedule
from runtime.lof.r2a_holdings import Holding, HoldingsSnapshot
from runtime.lof.r2a_shadow import LiveQuote
from runtime.lof.r2b2_low_vol_residual_shadow import (
    PROFILE,
    calculate_shadow_row,
    is_validated_low_vol_residual,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _holdings(now):
    rows = (
        Holding("A", "sh600001", "600001", "A", Decimal("0.40")),
        Holding("A", "sz000002", "000002", "B", Decimal("0.3228")),
    )
    return HoldingsSnapshot(
        fund_code="165508",
        as_of_date=date(2026, 6, 30),
        first_seen_at=now,
        fetched_at=now,
        holdings=rows,
        total_weight=Decimal("0.7228"),
        identity="test",
    )


def _allocation(now, *, stock="0.723", bond="0.1178", cash="0.174"):
    return AssetAllocationSnapshot(
        fund_code="165508",
        as_of_date=date(2026, 6, 30),
        fetched_at=now,
        stock_weight=Decimal(stock),
        bond_weight=Decimal(bond),
        cash_weight=Decimal(cash),
    )


def _distribution(now):
    return DistributionSchedule(
        fund_code="165508",
        fetched_at=now,
        events=(),
    )


def _snapshot(*, lag="T-1", quote_status="FRESH"):
    return {
        "snapshot_id": "main-1",
        "rows": [
            {
                "code": "165508",
                "name": "中信保诚深度LOF",
                "price": 2.0,
                "quote_status": quote_status,
                "official_nav": 1.9,
                "official_nav_date": "2026-09-29",
                "official_nav_lag_label": lag,
            }
        ],
    }


def _quotes(now):
    return {
        "sh600001": LiveQuote(
            "sh600001", Decimal("102"), Decimal("100"), now, None
        ),
        "sz000002": LiveQuote(
            "sz000002", Decimal("99"), Decimal("100"), now, None
        ),
    }


class R2B2LowVolResidualShadowTests(unittest.TestCase):
    def test_profile_contains_cross_source_validation(self):
        b = PROFILE["backtest"]
        self.assertLessEqual(b["tencent_mae_pct"], 0.15)
        self.assertLessEqual(b["sina_mae_pct"], 0.15)

    def test_validated_structure_gate(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        self.assertTrue(is_validated_low_vol_residual(_allocation(now)))
        self.assertFalse(
            is_validated_low_vol_residual(
                _allocation(now, stock="0.55", bond="0.30", cash="0.15")
            )
        )

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
        self.assertIsNotNone(row["shadow_estimated_nav"])
        self.assertIsNotNone(row["shadow_premium_rate"])
        self.assertFalse(row["eligible_for_main"])

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

    def test_structure_drift_fails_closed(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(),
            holdings=_holdings(now),
            allocation=_allocation(
                now,
                stock="0.55",
                bond="0.30",
                cash="0.15",
            ),
            distribution=_distribution(now),
            quotes=_quotes(now),
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(
            row["error"],
            "ASSET_ALLOCATION_NOT_VALIDATED_RESIDUAL",
        )


if __name__ == "__main__":
    unittest.main()
