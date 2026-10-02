import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.index_quote import IndexQuote
from runtime.lof.r5_cross_border_index_shadow import (
    PROFILE,
    calculate_shadow_row,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _snapshot(*, nav_date="2026-09-29", quote_status="FRESH"):
    return {
        "snapshot_id": "main-test",
        "rows": [
            {
                "code": "501025",
                "name": "香港银行LOF",
                "price": 1.02,
                "quote_status": quote_status,
                "official_nav": 1.00,
                "official_nav_date": nav_date,
                "official_nav_lag_label": "T-1",
            }
        ],
    }


def _quote(
    when,
    *,
    current="101",
    previous_close="100",
):
    return IndexQuote(
        symbol="sh000869",
        code="000869",
        name="HK银行",
        current=Decimal(current),
        previous_close=Decimal(previous_close),
        quote_time=when,
        source="TENCENT_QUOTE",
        error=None,
    )


class R5CrossBorderIndexShadowTests(unittest.TestCase):
    def test_profile_uses_official_relay_index(self):
        self.assertEqual(PROFILE["index_code"], "000869")
        self.assertEqual(PROFILE["tencent_symbol"], "sh000869")
        self.assertEqual(PROFILE["exposure_ratio"], Decimal("0.95"))

    def test_fresh_exact_anchor_produces_shadow_only_estimate(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(),
            quote=_quote(now),
            expected_anchor_date=date(2026, 9, 29),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(row["shadow_estimated_nav"], 1.0095, places=8)
        self.assertIsNotNone(row["shadow_premium_rate"])
        self.assertFalse(row["eligible_for_main"])

    def test_t2_nav_fails_closed(self):
        now = datetime(2026, 10, 2, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(nav_date="2026-09-29"),
            quote=_quote(
                datetime(2026, 9, 30, 16, 14, tzinfo=SHANGHAI_TZ)
            ),
            expected_anchor_date=date(2026, 9, 30),
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(
            row["error"],
            "OFFICIAL_NAV_NOT_PREVIOUS_TRADING_DAY",
        )

    def test_stale_index_quote_never_outputs_premium(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(),
            quote=_quote(
                datetime(2026, 9, 30, 13, 0, tzinfo=SHANGHAI_TZ)
            ),
            expected_anchor_date=date(2026, 9, 29),
            as_of=now,
            max_quote_age_seconds=180,
        )
        self.assertEqual(row["status"], "STALE")
        self.assertIsNotNone(row["shadow_estimated_nav"])
        self.assertIsNone(row["shadow_premium_rate"])

    def test_stale_lof_quote_blocks_shadow_premium(self):
        now = datetime(2026, 9, 30, 14, 30, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(quote_status="STALE"),
            quote=_quote(now),
            expected_anchor_date=date(2026, 9, 29),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertIsNone(row["shadow_premium_rate"])


if __name__ == "__main__":
    unittest.main()
