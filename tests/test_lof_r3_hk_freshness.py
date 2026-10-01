from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from runtime.lof.qdii_index_resolver import resolve_r3_qdii_index_bridge

TZ = ZoneInfo("Asia/Shanghai")

class R3HkFreshnessTest(unittest.TestCase):
    def _resolve(self, *, proxy_age: int, fx_age: int):
        now = datetime(2026, 10, 1, 9, 30, tzinfo=TZ)
        return resolve_r3_qdii_index_bridge(
            fund_code="160924",
            official_nav=Decimal("1"),
            official_nav_date=date(2026, 9, 30),
            proxy_anchor_close=Decimal("24000"),
            proxy_latest_close=Decimal("24240"),
            proxy_latest_date=date(2026, 10, 1),
            proxy_id="hkHSI",
            fx_anchor=Decimal("0.85"),
            fx_current=Decimal("0.851"),
            as_of=now,
            exposure_ratio=Decimal("1.0"),
            proxy_exactness="EXACT_INDEX",
            timing_quality="HIGH",
            proxy_time=now-timedelta(seconds=proxy_age),
            fx_time=now-timedelta(seconds=fx_age),
            max_proxy_age_seconds=180,
            enforce_realtime_freshness=True,
            resolver_method="HK_LIVE_INDEX_FX_BRIDGE",
        )

    def test_fresh_hk_inputs_are_available_high(self):
        row=self._resolve(proxy_age=30,fx_age=20)
        self.assertEqual(row.estimated_nav_status,"AVAILABLE")
        self.assertEqual(row.estimated_nav_quality,"HIGH")
        self.assertEqual(row.resolver_method,"HK_LIVE_INDEX_FX_BRIDGE")
        self.assertIsNotNone(row.estimated_nav_time)

    def test_stale_hk_index_makes_estimate_stale(self):
        row=self._resolve(proxy_age=600,fx_age=20)
        self.assertEqual(row.estimated_nav_status,"STALE")
        self.assertEqual(row.estimated_nav_quality,"HIGH")
        self.assertEqual(row.resolver_method,"HK_LIVE_INDEX_FX_BRIDGE")

if __name__ == "__main__":
    unittest.main()
