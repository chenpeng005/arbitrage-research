from __future__ import annotations

import unittest

from runtime.lof.estimate_reliability import (
    is_domestic_market_refresh_time,
    is_reliable_available_estimate,
)


class LofEstimateReliabilityTest(unittest.TestCase):
    def test_domestic_component_window(self) -> None:
        self.assertTrue(
            is_domestic_market_refresh_time(
                "2026-09-30T14:59:30+08:00"
            )
        )
        self.assertFalse(
            is_domestic_market_refresh_time(
                "2026-09-30T15:25:10+08:00"
            )
        )
        self.assertFalse(
            is_domestic_market_refresh_time(
                "2026-10-03T10:00:00+08:00"
            )
        )

    def test_component_post_close_available_is_not_reliable(self) -> None:
        row = {
            "estimated_nav": 1.05,
            "estimated_nav_status": "AVAILABLE",
            "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
            "estimated_nav_time": "2026-09-30T23:55:10+08:00",
        }
        self.assertFalse(is_reliable_available_estimate(row))
        row["estimated_nav_time"] = "2026-09-30T14:59:10+08:00"
        self.assertTrue(is_reliable_available_estimate(row))

    def test_non_component_method_is_not_market_window_limited(self) -> None:
        row = {
            "estimated_nav": 1.05,
            "estimated_nav_status": "AVAILABLE",
            "estimated_nav_method": "MULTIDAY_PROXY_FX_BRIDGE",
            "estimated_nav_time": "2026-09-30T23:55:10+08:00",
        }
        self.assertTrue(is_reliable_available_estimate(row))


if __name__ == "__main__":
    unittest.main()
