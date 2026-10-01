from __future__ import annotations

from datetime import date, datetime, timedelta
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.r2c_nav_band import (
    BondNavBandProfile,
    NavPoint,
    build_profile_from_returns,
    calculate_band_rows,
    classify_group,
    fetch_nav_history,
    profiles_requiring_refresh,
)


TZ = ZoneInfo("Asia/Shanghai")


def profile(
    code: str,
    *,
    group: str = "C1_ULTRA_LOW",
    up95: float = 0.10,
    up99: float = 0.20,
    history_end: date = date(2026, 9, 30),
) -> BondNavBandProfile:
    return BondNavBandProfile(
        fund_code=code,
        fetched_at=datetime(2026, 10, 1, 8, 0, tzinfo=TZ),
        history_end_date=history_end,
        return_sample_count=279,
        walk_forward_count=159,
        mae_abs_return=0.04,
        up95=up95,
        up99=up99,
        walk_forward_exceed95=4.5,
        walk_forward_exceed99=1.5,
        group=group,
        reliability=(
            "MEDIUM"
            if group in {"C1_ULTRA_LOW", "C2_LOW"}
            else "RESEARCH_ONLY"
        ),
    )


def market_row(
    code: str,
    *,
    lag: str = "T-1",
    quote_status: str = "FRESH",
    nav_date: str = "2026-09-30",
) -> dict:
    return {
        "code": code,
        "name": code,
        "resolver_class": "R2_DOMESTIC_OTHER",
        "lof_type": "BOND",
        "subscription_status": "OPEN",
        "official_nav": 1.0,
        "official_nav_date": nav_date,
        "official_nav_lag_label": lag,
        "price": 1.02,
        "quote_status": quote_status,
    }


class R2CNavBandTest(unittest.TestCase):
    def test_group_boundaries(self) -> None:
        self.assertEqual(classify_group(0.10), "C1_ULTRA_LOW")
        self.assertEqual(classify_group(0.11), "C2_LOW")
        self.assertEqual(classify_group(0.25), "C2_LOW")
        self.assertEqual(classify_group(0.30), "C3_MODERATE")
        self.assertEqual(classify_group(0.60), "C4_HIGH")

    def test_profile_builds_without_lookahead(self) -> None:
        start = date(2025, 1, 1)
        rows = []
        pattern = [0.01, 0.02, 0.03, 0.04, -0.02]
        for i in range(280):
            rows.append(
                (
                    start + timedelta(days=i),
                    pattern[i % len(pattern)],
                )
            )
        p = build_profile_from_returns(
            "166016",
            rows,
            fetched_at=datetime(2026, 10, 1, 8, 0, tzinfo=TZ),
        )
        self.assertEqual(p.group, "C1_ULTRA_LOW")
        self.assertGreater(p.walk_forward_count, 0)
        self.assertLessEqual(p.up95, 0.10)

    def test_available_band_formula(self) -> None:
        rows = calculate_band_rows(
            main_snapshot={
                "rows": [market_row("166016")]
            },
            profiles={
                "166016": profile(
                    "166016",
                    up95=0.10,
                    up99=0.20,
                )
            },
        )
        row = rows[0]
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(
            row["nav_upper_95"],
            1.001,
            places=8,
        )
        self.assertAlmostEqual(
            row["nav_upper_99"],
            1.002,
            places=8,
        )
        expected95 = (1.02 / 1.001 - 1.0) * 100
        expected99 = (1.02 / 1.002 - 1.0) * 100
        self.assertAlmostEqual(
            row["premium_floor_95"],
            expected95,
            places=8,
        )
        self.assertAlmostEqual(
            row["premium_floor_99"],
            expected99,
            places=8,
        )

    def test_c3_is_research_only(self) -> None:
        rows = calculate_band_rows(
            main_snapshot={
                "rows": [market_row("161216")]
            },
            profiles={
                "161216": profile(
                    "161216",
                    group="C3_MODERATE",
                    up95=0.30,
                    up99=0.40,
                )
            },
        )
        row = rows[0]
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["error"], "BAND_TOO_WIDE")

    def test_t2_nav_fails_closed(self) -> None:
        rows = calculate_band_rows(
            main_snapshot={
                "rows": [market_row("166016", lag="T-2")]
            },
            profiles={"166016": profile("166016")},
        )
        self.assertEqual(rows[0]["status"], "UNAVAILABLE")
        self.assertEqual(
            rows[0]["error"],
            "OFFICIAL_NAV_NOT_T1",
        )

    def test_stale_quote_or_profile_is_stale(self) -> None:
        rows = calculate_band_rows(
            main_snapshot={
                "rows": [
                    market_row(
                        "166016",
                        quote_status="STALE",
                    )
                ]
            },
            profiles={"166016": profile("166016")},
        )
        self.assertEqual(rows[0]["status"], "STALE")
        self.assertEqual(
            rows[0]["error"],
            "MARKET_QUOTE_STALE",
        )

        rows2 = calculate_band_rows(
            main_snapshot={
                "rows": [
                    market_row(
                        "166016",
                        nav_date="2026-10-01",
                    )
                ]
            },
            profiles={
                "166016": profile(
                    "166016",
                    history_end=date(2026, 9, 30),
                )
            },
        )
        self.assertEqual(rows2[0]["status"], "STALE")
        self.assertEqual(rows2[0]["error"], "PROFILE_LAGGED")

    @patch("runtime.lof.r2c_nav_band._fetch_nav_page")
    def test_incremental_history_stops_after_overlap(
        self,
        fetch_page_mock,
    ) -> None:
        start = date(2026, 4, 24)
        history = tuple(
            NavPoint(
                day=start + timedelta(days=i),
                nav=1.0 + i * 0.0001,
                distribution=0.0,
            )
            for i in range(160)
        )
        previous = BondNavBandProfile(
            fund_code="166016",
            fetched_at=datetime(2026, 9, 30, 20, 0, tzinfo=TZ),
            history_end_date=history[-1].day,
            return_sample_count=159,
            walk_forward_count=39,
            mae_abs_return=0.02,
            up95=0.05,
            up99=0.08,
            walk_forward_exceed95=5.0,
            walk_forward_exceed99=1.0,
            group="C1_ULTRA_LOW",
            reliability="MEDIUM",
            nav_history=history,
        )
        new_day = history[-1].day + timedelta(days=1)
        fetch_page_mock.return_value = [
            NavPoint(new_day, 1.02, 0.0),
            history[-1],
            history[-2],
        ]

        merged = fetch_nav_history(
            "166016",
            previous=previous,
            timeout=1,
        )
        self.assertEqual(fetch_page_mock.call_count, 1)
        self.assertEqual(merged[-1].day, new_day)
        self.assertEqual(len(merged), 161)

    def test_refresh_gate_uses_official_nav_date(self) -> None:
        main = {
            "rows": [
                market_row(
                    "166016",
                    nav_date="2026-09-30",
                ),
                market_row(
                    "161820",
                    nav_date="2026-09-30",
                ),
            ]
        }
        profiles = {
            "166016": profile(
                "166016",
                history_end=date(2026, 9, 30),
            ),
            "161820": profile(
                "161820",
                history_end=date(2026, 9, 29),
            ),
        }
        self.assertEqual(
            profiles_requiring_refresh(main, profiles),
            ["161820"],
        )


if __name__ == "__main__":
    unittest.main()
