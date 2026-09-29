from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.resolver import ResolverInput, resolve_estimated_nav


TZ = ZoneInfo("Asia/Shanghai")


class LofEstimatedNavResolverTest(unittest.TestCase):
    def setUp(self) -> None:
        self.anchor_time = datetime(2026, 9, 28, 15, 0, tzinfo=TZ)
        self.current_time = datetime(2026, 9, 29, 10, 0, tzinfo=TZ)

    def test_domestic_index_full_exposure(self) -> None:
        result = resolve_estimated_nav(
            ResolverInput(
                fund_code="163407",
                resolver_class="R1_DOMESTIC_INDEX",
                resolver_method="INDEX_PROXY",
                proxy_id="000300",
                official_nav=Decimal("2.6000"),
                proxy_anchor_value=Decimal("4300"),
                proxy_current_value=Decimal("4343"),
                proxy_anchor_time=self.anchor_time,
                proxy_current_time=self.current_time,
                as_of=self.current_time,
                max_proxy_age_seconds=60,
                exposure_ratio=Decimal("1"),
                quality="HIGH",
            )
        )
        self.assertEqual(result.estimated_nav_status, "AVAILABLE")
        self.assertEqual(result.estimated_nav, Decimal("2.62600"))
        self.assertEqual(result.proxy_return, Decimal("0.01"))
        self.assertEqual(result.estimated_nav_quality, "HIGH")

    def test_missing_exposure_caps_quality_at_medium(self) -> None:
        result = resolve_estimated_nav(
            ResolverInput(
                fund_code="163407",
                resolver_class="R1_DOMESTIC_INDEX",
                resolver_method="INDEX_PROXY",
                proxy_id="000300",
                official_nav=Decimal("2.6000"),
                proxy_anchor_value=Decimal("4300"),
                proxy_current_value=Decimal("4343"),
                proxy_anchor_time=self.anchor_time,
                proxy_current_time=self.current_time,
                as_of=self.current_time,
                max_proxy_age_seconds=60,
                exposure_ratio=None,
                quality="HIGH",
            )
        )
        self.assertEqual(result.estimated_nav_quality, "MEDIUM")

    def test_qdii_index_applies_fx(self) -> None:
        result = resolve_estimated_nav(
            ResolverInput(
                fund_code="161128",
                resolver_class="R3_QDII_INDEX",
                resolver_method="INDEX_PROXY",
                proxy_id="SP500-IT",
                official_nav=Decimal("7.0000"),
                proxy_anchor_value=Decimal("1000"),
                proxy_current_value=Decimal("1020"),
                proxy_anchor_time=self.anchor_time,
                proxy_current_time=self.current_time,
                as_of=self.current_time,
                max_proxy_age_seconds=60,
                exposure_ratio=Decimal("1"),
                fx_anchor=Decimal("7.10"),
                fx_current=Decimal("7.171"),
                quality="HIGH",
            )
        )
        self.assertEqual(result.estimated_nav_status, "AVAILABLE")
        self.assertEqual(result.proxy_return, Decimal("0.02"))
        self.assertEqual(result.fx_return, Decimal("0.01"))
        self.assertEqual(result.estimated_nav, Decimal("7.21140000"))

    def test_stale_proxy_is_not_available_for_realtime_sort(self) -> None:
        stale_time = datetime(2026, 9, 29, 9, 50, tzinfo=TZ)
        result = resolve_estimated_nav(
            ResolverInput(
                fund_code="163407",
                resolver_class="R1_DOMESTIC_INDEX",
                resolver_method="INDEX_PROXY",
                proxy_id="000300",
                official_nav=Decimal("2.6"),
                proxy_anchor_value=Decimal("4300"),
                proxy_current_value=Decimal("4343"),
                proxy_anchor_time=self.anchor_time,
                proxy_current_time=stale_time,
                as_of=self.current_time,
                max_proxy_age_seconds=60,
                exposure_ratio=Decimal("1"),
                quality="HIGH",
            )
        )
        self.assertEqual(result.estimated_nav_status, "STALE")

    def test_invalid_inputs_fail_closed(self) -> None:
        result = resolve_estimated_nav(
            ResolverInput(
                fund_code="x",
                resolver_class="R1_DOMESTIC_INDEX",
                resolver_method="INDEX_PROXY",
                proxy_id="x",
                official_nav=None,
                proxy_anchor_value=Decimal("1"),
                proxy_current_value=Decimal("1"),
                proxy_anchor_time=self.anchor_time,
                proxy_current_time=self.current_time,
                as_of=self.current_time,
                max_proxy_age_seconds=60,
            )
        )
        self.assertEqual(result.estimated_nav_status, "UNAVAILABLE")
        self.assertIsNone(result.estimated_nav)


if __name__ == "__main__":
    unittest.main()
