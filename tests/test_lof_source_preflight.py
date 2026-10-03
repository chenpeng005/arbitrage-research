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

    def test_off_hours_stale_estimates_count_as_observable(self) -> None:
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
            checked_at=datetime(2026, 10, 1, 14, 0, tzinfo=TZ),
        )
        self.assertEqual(result["status"], "PASS")
        check = next(
            x for x in result["checks"]
            if x["name"] == "estimated_nav_nonzero"
        )
        self.assertEqual(check["value"], 100)
        self.assertEqual(check["threshold"], "available+stale>0")

    def test_market_hours_all_stale_estimates_fail(self) -> None:
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

    def test_r1_nav_date_lag_fails_even_with_full_nav_count(self) -> None:
        snapshot = _snapshot()
        snapshot["rows"] = [
            {
                "code": str(i),
                "resolver_class": "R1_DOMESTIC_INDEX" if i < 100 else "R2_DOMESTIC_OTHER",
                "official_nav_date": (
                    "2026-09-29" if i < 100 else "2026-09-30"
                ),
            }
            for i in range(400)
        ]
        result = evaluate_preflight(
            snapshot=snapshot,
            r1_context_resolved=100,
            r1_context_unresolved=0,
            expect_fresh_quotes=False,
            application_commit_sha="abc",
            checked_at=datetime(2026, 10, 3, 11, 0, tzinfo=TZ),
            source_transport={"expected_nav_date": "2026-09-30"},
        )
        self.assertEqual(result["status"], "FAIL")
        check = next(
            x for x in result["checks"]
            if x["name"] == "r1_official_nav_expected_date_coverage"
        )
        self.assertEqual(check["detail"], "0/100")

    def test_r1_nav_expected_date_coverage_passes_after_recovery(self) -> None:
        snapshot = _snapshot()
        snapshot["rows"] = [
            {
                "code": str(i),
                "resolver_class": "R1_DOMESTIC_INDEX" if i < 100 else "R2_DOMESTIC_OTHER",
                "official_nav_date": "2026-09-30",
            }
            for i in range(400)
        ]
        result = evaluate_preflight(
            snapshot=snapshot,
            r1_context_resolved=100,
            r1_context_unresolved=0,
            expect_fresh_quotes=False,
            application_commit_sha="abc",
            checked_at=datetime(2026, 10, 3, 11, 0, tzinfo=TZ),
            source_transport={"expected_nav_date": "2026-09-30"},
        )
        self.assertEqual(result["status"], "PASS")

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
