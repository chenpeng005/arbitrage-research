import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.r5_hsi_feeder_shadow import calculate_shadow_row


TZ = ZoneInfo("Asia/Shanghai")


def _snapshot(*, nav_date="2026-09-29", quote_status="FRESH"):
    return {
        "snapshot_id": "main-1",
        "rows": [{
            "code": "501302",
            "name": "恒生指数基金LOF",
            "price": 1.12,
            "quote_status": quote_status,
            "official_nav": 1.10,
            "official_nav_date": nav_date,
        }],
    }


class R5HSIFeederShadowTests(unittest.TestCase):
    def test_fresh_hsi_generates_shadow_only_estimate(self):
        now = datetime(2026, 9, 30, 14, 0, tzinfo=TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(),
            proxy_anchor=Decimal("26000"),
            proxy_current=Decimal("26260"),
            proxy_time=datetime(2026, 9, 30, 13, 59, tzinfo=TZ),
            proxy_error=None,
            expected_anchor_date=date(2026, 9, 29),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(row["shadow_estimated_nav"], 1.11045, places=6)
        self.assertIsNotNone(row["shadow_premium_rate"])
        self.assertFalse(row["eligible_for_main"])

    def test_non_previous_day_nav_fails_closed(self):
        now = datetime(2026, 10, 2, 14, 0, tzinfo=TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(nav_date="2026-09-29"),
            proxy_anchor=Decimal("26000"),
            proxy_current=Decimal("26260"),
            proxy_time=now,
            proxy_error=None,
            expected_anchor_date=date(2026, 9, 30),
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(
            row["error"], "OFFICIAL_NAV_NOT_PREVIOUS_TRADING_DAY"
        )

    def test_stale_hsi_does_not_generate_premium(self):
        now = datetime(2026, 10, 2, 14, 0, tzinfo=TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(nav_date="2026-09-30"),
            proxy_anchor=Decimal("26000"),
            proxy_current=Decimal("26260"),
            proxy_time=datetime(2026, 10, 1, 16, 0, tzinfo=TZ),
            proxy_error=None,
            expected_anchor_date=date(2026, 9, 30),
            as_of=now,
        )
        self.assertEqual(row["status"], "STALE")
        self.assertIsNotNone(row["shadow_estimated_nav"])
        self.assertIsNone(row["shadow_premium_rate"])

    def test_missing_anchor_fails_closed(self):
        now = datetime(2026, 9, 30, 14, 0, tzinfo=TZ)
        row = calculate_shadow_row(
            main_snapshot=_snapshot(),
            proxy_anchor=None,
            proxy_current=Decimal("26260"),
            proxy_time=now,
            proxy_error=None,
            expected_anchor_date=date(2026, 9, 29),
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["error"], "HSI_ANCHOR_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
