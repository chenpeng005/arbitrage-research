from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

from runtime.lof.estimate_freshness_audit import (
    build_cadence_profile,
    build_freshness_audit,
    market_context,
)
from runtime.lof.snapshot_archive import write_snapshot_gzip


class EstimateFreshnessAuditTest(unittest.TestCase):
    def test_holiday_stale_is_not_misclassified_as_active_failure(self):
        snapshot = {
            "snapshot_id": "runtime-20261004T051800",
            "generated_at": "2026-10-04T05:18:00+08:00",
            "rows": [
                {
                    "code": "501016",
                    "name": "券商基金LOF",
                    "resolver_class": "R1_DOMESTIC_INDEX",
                    "lof_type": "EQUITY",
                    "quote_status": "STALE",
                    "quote_time": "2026-09-30T15:00:00+08:00",
                    "estimated_nav_status": "STALE",
                    "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                    "estimated_model_id": "R1_INDEX_PROXY",
                    "estimated_model_version": "R1_INDEX_PROXY_V1",
                    "estimated_nav": 1.0,
                    "estimated_nav_time": "2026-09-30T15:00:00+08:00",
                    "estimated_nav_proxy_time": "2026-09-30T15:00:00+08:00",
                }
            ],
        }
        context = market_context(snapshot)
        self.assertEqual(context["state"], "OFF_MARKET_OR_HOLIDAY")
        audit = build_freshness_audit(snapshot=snapshot)
        row = audit["rows"][0]
        self.assertEqual(row["coverage_state"], "MAIN_MODEL_CONFIGURED")
        self.assertEqual(row["update_state"], "EXPECTED_OFF_MARKET_STALE")
        self.assertEqual(row["blocker"], "OFF_MARKET_OR_HOLIDAY")

    def test_structural_no_model_and_configured_unavailable_are_separate(self):
        snapshot = {
            "snapshot_id": "runtime-20261004T051800",
            "generated_at": "2026-10-04T05:18:00+08:00",
            "rows": [
                {
                    "code": "501001",
                    "name": "财通精选混合LOF",
                    "resolver_class": "R2_DOMESTIC_OTHER",
                    "lof_type": "MIXED",
                    "quote_status": "STALE",
                    "quote_time": "2026-09-30T15:00:00+08:00",
                    "estimated_nav_status": "UNAVAILABLE",
                    "estimated_nav_method": "UNAVAILABLE",
                    "estimated_nav_error": "R2_NOT_PROMOTED_TO_MAIN",
                },
                {
                    "code": "501219",
                    "name": "智胜先锋LOF",
                    "resolver_class": "R2_DOMESTIC_OTHER",
                    "lof_type": "EQUITY",
                    "quote_status": "STALE",
                    "quote_time": "2026-09-30T15:00:00+08:00",
                    "estimated_nav_status": "UNAVAILABLE",
                    "estimated_nav_method": "DISCLOSED_HOLDINGS_BASKET",
                    "estimated_model_id": "R2A_HOLDINGS_BASKET",
                    "estimated_model_version": "R2A_HOLDINGS_BASKET_V1",
                    "estimated_nav_error": "R2_PROMOTED_INPUT_UNAVAILABLE",
                },
            ],
        }
        audit = build_freshness_audit(snapshot=snapshot)
        by_code = {row["code"]: row for row in audit["rows"]}
        self.assertEqual(
            by_code["501001"]["update_state"],
            "STRUCTURAL_NO_MAIN_MODEL",
        )
        self.assertEqual(
            by_code["501219"]["update_state"],
            "CONFIGURED_BUT_UNAVAILABLE",
        )
        self.assertEqual(
            audit["summary"]["structural_no_main_model_count"],
            1,
        )
        self.assertEqual(
            audit["summary"]["configured_but_unavailable_count"],
            1,
        )

    def test_proxy_newer_than_estimate_is_flagged(self):
        snapshot = {
            "snapshot_id": "runtime-20261004T051800",
            "generated_at": "2026-10-04T05:18:00+08:00",
            "rows": [
                {
                    "code": "160140",
                    "name": "美国REIT精选LOF",
                    "resolver_class": "R3_QDII_INDEX",
                    "lof_type": "QDII_EQUITY",
                    "quote_status": "STALE",
                    "quote_time": "2026-09-30T15:00:00+08:00",
                    "estimated_nav_status": "STALE",
                    "estimated_nav_method": "US_LAST_CLOSE_FX_BRIDGE",
                    "estimated_model_id": "R3_US_LAST_CLOSE_FX",
                    "estimated_model_version": "R3_US_LAST_CLOSE_FX_V1",
                    "estimated_nav_time": "2026-10-01T03:00:00+08:00",
                    "estimated_nav_proxy_time": "2026-10-03T04:00:00+08:00",
                }
            ],
        }
        audit = build_freshness_audit(snapshot=snapshot)
        self.assertIn(
            "FX_OLDER_THAN_PROXY_LIMITS_FRESHNESS",
            audit["rows"][0]["diagnostic_flags"],
        )

    def test_r2_promoted_off_market_waits_for_next_active_session(self):
        snapshot = {
            "snapshot_id": "runtime-20261004T051800",
            "generated_at": "2026-10-04T05:18:00+08:00",
            "rows": [
                {
                    "code": "501219",
                    "name": "智胜先锋LOF",
                    "resolver_class": "R2_DOMESTIC_OTHER",
                    "lof_type": "EQUITY",
                    "quote_status": "STALE",
                    "quote_time": "2026-09-30T15:00:00+08:00",
                    "official_nav_date": "2026-09-30",
                    "official_nav_lag_label": "T-1",
                    "estimated_nav_status": "UNAVAILABLE",
                    "estimated_nav_method": "DISCLOSED_HOLDINGS_BASKET",
                    "estimated_model_id": "R2A_HOLDINGS_BASKET",
                    "estimated_model_version": "R2A_HOLDINGS_BASKET_V1",
                    "estimated_nav_error": "R2_PROMOTED_INPUT_UNAVAILABLE",
                }
            ],
        }
        shadow_registry = {
            "rows": {
                "501219": {
                    "shadow_status": "UNAVAILABLE",
                    "shadow_latest_generated_at": "2026-10-03T07:49:40+08:00",
                    "models": [
                        {
                            "method": "DISCLOSED_HOLDINGS_BASKET",
                            "error": "OFFICIAL_NAV_NOT_T1",
                        }
                    ],
                }
            }
        }
        audit = build_freshness_audit(
            snapshot=snapshot,
            shadow_registry=shadow_registry,
        )
        row = audit["rows"][0]
        self.assertEqual(row["update_state"], "WAITING_NEXT_ACTIVE_SESSION")
        self.assertEqual(
            row["blocker"],
            "R2_SHADOW_NAV_SYNC_WAITS_FOR_FRESH_MARKET",
        )
        self.assertEqual(
            audit["summary"]["waiting_next_active_session_count"],
            1,
        )

    def test_cadence_profile_measures_unique_estimate_updates(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directory = root / "snapshots"
            directory.mkdir(parents=True, exist_ok=True)
            for index, sec in enumerate((0, 30, 60, 90)):
                hh = 10
                mm = sec // 60
                ss = sec % 60
                stamp = f"20260930T{hh:02d}{mm:02d}{ss:02d}"
                estimate_time = (
                    f"2026-09-30T{hh:02d}:{mm:02d}:{ss:02d}+08:00"
                )
                rows = []
                for n in range(60):
                    rows.append(
                        {
                            "code": f"50{n:04d}",
                            "name": f"F{n}",
                            "resolver_class": "R1_DOMESTIC_INDEX",
                            "quote_status": "FRESH",
                            "quote_time": estimate_time,
                            "estimated_nav_status": "AVAILABLE",
                            "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                            "estimated_model_version": "R1_INDEX_PROXY_V1",
                            "estimated_nav_time": estimate_time,
                        }
                    )
                snapshot = {
                    "snapshot_id": f"runtime-{stamp}",
                    "generated_at": estimate_time,
                    "rows": rows,
                }
                write_snapshot_gzip(
                    directory / f"runtime-{stamp}.json.gz",
                    json.dumps(snapshot),
                )

            # Add enough files for baseline-selection minimum.
            for index in range(16):
                sec = 120 + index * 30
                mm = sec // 60
                ss = sec % 60
                stamp = f"20260930T10{mm:02d}{ss:02d}"
                estimate_time = (
                    f"2026-09-30T10:{mm:02d}:{ss:02d}+08:00"
                )
                rows = [
                    {
                        "code": f"50{n:04d}",
                        "name": f"F{n}",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "quote_status": "FRESH",
                        "quote_time": estimate_time,
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                        "estimated_model_version": "R1_INDEX_PROXY_V1",
                        "estimated_nav_time": estimate_time,
                    }
                    for n in range(60)
                ]
                snapshot = {
                    "snapshot_id": f"runtime-{stamp}",
                    "generated_at": estimate_time,
                    "rows": rows,
                }
                write_snapshot_gzip(
                    directory / f"runtime-{stamp}.json.gz",
                    json.dumps(snapshot),
                )

            profile = build_cadence_profile(
                root,
                baseline_date=date(2026, 9, 30),
            )
            self.assertEqual(profile["baseline_date"], "2026-09-30")
            self.assertEqual(profile["snapshot_files_read"], 20)
            method = profile["methods"]["INDEX_PROXY_PREV_CLOSE"]
            self.assertEqual(method["fund_count"], 60)
            self.assertAlmostEqual(
                method["median_update_seconds"],
                30.0,
                places=6,
            )


if __name__ == "__main__":
    unittest.main()
