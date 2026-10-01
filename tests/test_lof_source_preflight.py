from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from runtime.lof.source_preflight import evaluate_preflight


TZ = ZoneInfo("Asia/Shanghai")


def _snapshot(
    *,
    universe: int = 400,
    quote_fresh: int = 398,
    quote_stale: int = 0,
    quote_unavailable: int = 2,
    nav: int = 390,
    state: int = 395,
    estimated: int = 100,
    estimated_stale: int = 0,
    lane_errors: dict | None = None,
) -> dict:
    return {
        "collector_status": "PASS" if not lane_errors else "DEGRADED",
        "universe_count": universe,
        "rows": [{"code": str(i)} for i in range(universe)],
        "quality_summary": {
            "quote_fresh_count": quote_fresh,
            "quote_stale_count": quote_stale,
            "quote_unavailable_count": quote_unavailable,
            "official_nav_available_count": nav,
            "official_nav_unavailable_count": universe - nav,
            "state_available_count": state,
            "state_unavailable_count": universe - state,
            "estimated_nav_available_count": estimated,
            "estimated_nav_stale_count": estimated_stale,
            "estimated_nav_unavailable_count": (
                universe - estimated - estimated_stale
            ),
        },
        "lane_errors": lane_errors or {},
    }


class LofProductionSourcePreflightTest(unittest.TestCase):
    def test_good_market_hours_snapshot_passes(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=True,
            application_commit_sha="abc",
            checked_at=datetime(2026, 9, 29, 14, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["metrics"]["universe_count"], 400)

    def test_off_hours_can_skip_fresh_ratio(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(
                quote_fresh=0,
                quote_stale=398,
                quote_unavailable=2,
            ),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=False,
            application_commit_sha="abc",
            checked_at=datetime(2026, 9, 29, 18, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "PASS")
        check = next(
            x for x in result["checks"]
            if x["name"] == "quote_fresh_coverage"
        )
        self.assertEqual(check["status"], "SKIP")

    def test_off_hours_all_estimates_stale_still_passes_liveness(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(
                quote_fresh=0,
                quote_stale=398,
                quote_unavailable=2,
                estimated=0,
                estimated_stale=100,
            ),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=False,
            application_commit_sha="abc",
            checked_at=datetime(2026, 10, 1, 11, 30, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "PASS")
        lane_check = next(
            x for x in result["checks"]
            if x["name"] == "estimated_nav_nonzero"
        )
        fresh_check = next(
            x for x in result["checks"]
            if x["name"] == "estimated_nav_fresh_nonzero"
        )
        self.assertEqual(lane_check["status"], "PASS")
        self.assertEqual(lane_check["value"], 100)
        self.assertEqual(fresh_check["status"], "SKIP")

    def test_market_hours_require_fresh_estimated_nav(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(
                estimated=0,
                estimated_stale=100,
            ),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=True,
            application_commit_sha="abc",
            checked_at=datetime(2026, 9, 29, 14, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(
            any(
                x["name"] == "estimated_nav_fresh_nonzero"
                for x in result["errors"]
            )
        )

    def test_no_estimated_nav_output_fails_even_off_hours(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(
                quote_fresh=0,
                quote_stale=398,
                quote_unavailable=2,
                estimated=0,
                estimated_stale=0,
            ),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=False,
            application_commit_sha="abc",
            checked_at=datetime(2026, 10, 1, 11, 30, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(
            any(
                x["name"] == "estimated_nav_nonzero"
                for x in result["errors"]
            )
        )

    def test_low_nav_coverage_fails(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(nav=300),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=True,
            application_commit_sha="abc",
            checked_at=datetime(2026, 9, 29, 14, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(
            any(
                x["name"] == "official_nav_coverage"
                for x in result["errors"]
            )
        )

    def test_critical_lane_error_fails(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(
                lane_errors={"quote": "TimeoutError"},
            ),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=True,
            application_commit_sha="abc",
            checked_at=datetime(2026, 9, 29, 14, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "FAIL")

    def test_optional_lane_error_warns(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(
                lane_errors={"estimated_nav": "TimeoutError"},
            ),
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=True,
            application_commit_sha="abc",
            checked_at=datetime(2026, 9, 29, 14, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "WARN")

    def test_low_r1_mapping_coverage_fails(self) -> None:
        result = evaluate_preflight(
            snapshot=_snapshot(),
            r1_context_resolved=50,
            r1_context_unresolved=80,
            expect_fresh_quotes=True,
            application_commit_sha="abc",
            checked_at=datetime(2026, 9, 29, 14, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
