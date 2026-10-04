from __future__ import annotations

from datetime import date, datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


test_plan = _load_script("lof_test_plan.py")
golden = _load_script("lof_golden_replay.py")
historical = _load_script("lof_historical_replay.py")
targeted_live = _load_script("lof_targeted_live_probe.py")
reopen_acceptance = _load_script("lof_r2_reopen_acceptance.py")


class LofTestImpactPlannerTest(unittest.TestCase):
    def test_ui_change_is_small_and_has_no_live_scope(self):
        plan = test_plan.build_plan(
            ["runtime/lof/static/app.js"]
        )
        self.assertEqual(plan["domains"], ["ui"])
        self.assertEqual(plan["live_scope"], "NONE")
        self.assertFalse(plan["golden_required"])
        self.assertFalse(
            plan["full_live_preflight_required_before_release"]
        )
        self.assertIn(
            "tests.test_lof_static_method_labels",
            plan["selected_test_modules"],
        )

    def test_fx_change_is_targeted_not_full_live(self):
        plan = test_plan.build_plan(
            ["runtime/lof/fx_resolver.py"]
        )
        self.assertIn("r3_fx", plan["domains"])
        self.assertEqual(plan["live_scope"], "TARGETED")
        self.assertTrue(plan["golden_required"])
        self.assertTrue(plan["historical_replay_required"])
        self.assertFalse(
            plan["full_live_preflight_required_before_release"]
        )
        self.assertIn("R3_QDII_INDEX", plan["affected_resolver_classes"])
        self.assertIn("R5_SPECIAL", plan["affected_resolver_classes"])
        self.assertEqual(plan["targeted_live_probe_domains"], ["fx"])

    def test_common_collector_change_requires_full_live_preflight(self):
        plan = test_plan.build_plan(
            ["runtime/lof/nav.py"]
        )
        self.assertIn("collector", plan["domains"])
        self.assertEqual(plan["live_scope"], "FULL")
        self.assertTrue(
            plan["full_live_preflight_required_before_release"]
        )

    def test_shared_resolver_marks_full_regression_release_barrier(self):
        plan = test_plan.build_plan(
            ["runtime/lof/resolver.py"]
        )
        self.assertEqual(plan["tier"], "FULL_RELEASE_REQUIRED")
        self.assertTrue(
            plan["full_regression_required_before_release"]
        )
        # It is still not a reason to hit all external sources during
        # development.
        self.assertEqual(plan["live_scope"], "NONE")

    def test_unknown_runtime_file_fails_safe_to_full_release(self):
        plan = test_plan.build_plan(
            ["runtime/lof/future_new_shared_module.py"]
        )
        self.assertEqual(
            plan["unknown_lof_runtime"],
            ["runtime/lof/future_new_shared_module.py"],
        )
        self.assertEqual(plan["tier"], "FULL_RELEASE_REQUIRED")

    def test_changed_test_always_runs_itself(self):
        plan = test_plan.build_plan(
            ["tests/test_lof_fx_fallback.py"]
        )
        self.assertIn(
            "tests.test_lof_fx_fallback",
            plan["selected_test_modules"],
        )


class LofTargetedLiveProbeTest(unittest.TestCase):
    def test_infer_fx_anchor_date_uses_most_common_fx_dependent_nav(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            rows = [
                {
                    "code": "161125",
                    "estimated_nav_method": "US_LAST_CLOSE_CNH_FALLBACK_BRIDGE",
                    "official_nav_date": "2026-09-29",
                },
                {
                    "code": "501018",
                    "estimated_nav_method": "COMMODITY_BASKET_CNH_FALLBACK_BRIDGE",
                    "official_nav_date": "2026-09-29",
                },
                {
                    "code": "160717",
                    "estimated_nav_method": "HK_LIVE_INDEX_FX_BRIDGE",
                    "official_nav_date": "2026-09-30",
                },
            ]
            (root / "latest_market_snapshot.json").write_text(
                json.dumps({"rows": rows}),
                encoding="utf-8",
            )
            self.assertEqual(
                targeted_live.infer_nav_date(root),
                date(2026, 9, 29),
            )

    @patch.object(targeted_live, "fetch_tencent_fx_quote")
    @patch.object(targeted_live, "fx_close_on")
    @patch.object(targeted_live, "fetch_tencent_fx_daily")
    @patch.object(targeted_live, "resolve_usdcny_input")
    def test_fx_targeted_probe_checks_existing_resolvers_only(
        self,
        usd_mock,
        hkd_daily_mock,
        hkd_close_mock,
        hkd_quote_mock,
    ):
        now = datetime.fromisoformat("2026-10-04T10:00:00+08:00")
        usd_mock.return_value = SimpleNamespace(
            anchor=1,
            current=1,
            quote_time=now,
            error=None,
            status="STALE",
            source="TENCENT_CNY_ANCHOR_WSCN_CNH_RETURN",
            calibration=SimpleNamespace(status="PASS"),
        )
        hkd_daily_mock.return_value = [object()]
        hkd_close_mock.return_value = 1
        hkd_quote_mock.return_value = SimpleNamespace(
            current=1,
            quote_time=now,
            error=None,
            source="TENCENT_FX",
        )
        result = targeted_live.probe_fx(
            date(2026, 9, 29),
            timeout=1,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(result["checks"]), 2)
        usd_mock.assert_called_once()
        hkd_daily_mock.assert_called_once()


class LofR2ReopenAcceptanceTest(unittest.TestCase):
    def _write_snapshot(
        self,
        root: Path,
        *,
        stamp: str,
        generated_at: str,
        market_fresh: bool,
        available_codes: set[str],
        expected_anchor: str,
    ) -> None:
        from runtime.lof.snapshot_archive import write_snapshot_gzip

        rows = []
        for code in reopen_acceptance.promoted_codes():
            method = reopen_acceptance.promoted_r2_method(code)[1]
            rows.append(
                {
                    "code": code,
                    "name": f"F-{code}",
                    "quote_status": (
                        "FRESH" if market_fresh else "STALE"
                    ),
                    "official_nav_date": expected_anchor,
                    "estimated_nav_status": (
                        "AVAILABLE"
                        if code in available_codes
                        else "UNAVAILABLE"
                    ),
                    "estimated_nav_method": method,
                    "estimated_nav_error": (
                        None
                        if code in available_codes
                        else "R2_PROMOTED_INPUT_UNAVAILABLE"
                    ),
                    "estimated_nav_time": (
                        generated_at
                        if code in available_codes
                        else None
                    ),
                }
            )

        payload = {
            "snapshot_id": f"runtime-{stamp}",
            "generated_at": generated_at,
            "universe_count": len(rows),
            "quality_summary": {
                "quote_fresh_count": (
                    len(rows) if market_fresh else 0
                )
            },
            "rows": rows,
        }
        directory = root / "snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        write_snapshot_gzip(
            directory / f"runtime-{stamp}.json.gz",
            json.dumps(payload),
        )

    def test_reopen_acceptance_measures_end_to_end_lag(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codes = reopen_acceptance.promoted_codes()
            first_half = set(codes[:9])
            all_codes = set(codes)
            anchor = "2026-09-30"

            self._write_snapshot(
                root,
                stamp="20261008T092959",
                generated_at="2026-10-08T09:29:59+08:00",
                market_fresh=False,
                available_codes=set(),
                expected_anchor=anchor,
            )
            self._write_snapshot(
                root,
                stamp="20261008T093000",
                generated_at="2026-10-08T09:30:00+08:00",
                market_fresh=True,
                available_codes=set(),
                expected_anchor=anchor,
            )
            self._write_snapshot(
                root,
                stamp="20261008T093030",
                generated_at="2026-10-08T09:30:30+08:00",
                market_fresh=True,
                available_codes=first_half,
                expected_anchor=anchor,
            )
            self._write_snapshot(
                root,
                stamp="20261008T093100",
                generated_at="2026-10-08T09:31:00+08:00",
                market_fresh=True,
                available_codes=all_codes,
                expected_anchor=anchor,
            )

            result = reopen_acceptance.analyze_reopen(
                root,
                day=date(2026, 10, 8),
                expected_anchor_date=date(2026, 9, 30),
            )

            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["promoted_count"], 18)
            self.assertEqual(result["available_count"], 18)
            self.assertEqual(result["available_within_60s"], 18)
            self.assertEqual(result["available_within_120s"], 18)
            self.assertEqual(result["max_lag_seconds"], 60)
            self.assertEqual(result["missing_available_codes"], [])
            self.assertEqual(result["anchor_mismatch_codes"], [])

    def test_reopen_acceptance_remains_incomplete_if_one_never_recovers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            codes = reopen_acceptance.promoted_codes()
            missing = codes[-1]
            available = set(codes[:-1])
            anchor = "2026-09-30"

            self._write_snapshot(
                root,
                stamp="20261008T093000",
                generated_at="2026-10-08T09:30:00+08:00",
                market_fresh=True,
                available_codes=available,
                expected_anchor=anchor,
            )

            result = reopen_acceptance.analyze_reopen(
                root,
                day=date(2026, 10, 8),
                expected_anchor_date=date(2026, 9, 30),
            )

            self.assertEqual(result["status"], "INCOMPLETE")
            self.assertEqual(result["available_count"], 17)
            self.assertEqual(result["missing_available_codes"], [missing])


class LofGoldenReplayTest(unittest.TestCase):
    def test_committed_production_derived_golden_cases_pass(self):
        result = golden.run_fixture(
            ROOT / "tests/fixtures/lof_golden_replay_v1.json"
        )
        self.assertGreaterEqual(result["case_count"], 7)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(result["passed"], result["case_count"])


class LofHistoricalReplayTest(unittest.TestCase):
    def test_no_network_formula_replay_from_gzip_snapshot(self):
        from runtime.lof.snapshot_archive import write_snapshot_gzip

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directory = root / "snapshots"
            directory.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "snapshot_id": "runtime-20260930T150000",
                "generated_at": "2026-09-30T15:00:00+08:00",
                "universe_count": 1,
                "rows": [
                    {
                        "code": "501005",
                        "name": "精准医疗LOF",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                        "estimated_nav_quality": "MEDIUM",
                        "official_nav": 1.0,
                        "estimated_nav": 1.01,
                        "estimated_nav_proxy": "930719",
                        "estimated_nav_proxy_time": (
                            "2026-09-30T14:59:59+08:00"
                        ),
                        "estimated_nav_proxy_return": 0.01,
                        "estimated_nav_fx_return": None,
                        "estimated_nav_exposure_ratio": 1.0,
                        "estimated_nav_tracking_adjustment": 1.0,
                    }
                ],
            }
            write_snapshot_gzip(
                directory / "runtime-20260930T150000.json.gz",
                json.dumps(snapshot),
            )
            result = historical.run_replay(
                root,
                day="2026-09-30",
            )
            self.assertEqual(result["snapshot_files_read"], 1)
            self.assertEqual(result["schema_rows_checked"], 1)
            self.assertEqual(result["formula_rows_checked"], 1)
            self.assertTrue(result["formula_replay_available"])
            self.assertEqual(result["failed"], 0)


    def test_legacy_snapshot_without_formula_inputs_still_replays_schema(self):
        from runtime.lof.snapshot_archive import write_snapshot_gzip

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            directory = root / "snapshots"
            directory.mkdir(parents=True, exist_ok=True)
            snapshot = {
                "snapshot_id": "runtime-20260930T145010",
                "generated_at": "2026-09-30T14:50:10+08:00",
                "universe_count": 1,
                "rows": [
                    {
                        "code": "501005",
                        "name": "精准医疗LOF",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
                        "estimated_nav_status": "AVAILABLE",
                        "official_nav": 1.089,
                        "estimated_nav": 1.1265,
                        "estimated_nav_time": "2026-09-30T14:50:00+08:00",
                    }
                ],
            }
            write_snapshot_gzip(
                directory / "runtime-20260930T145010.json.gz",
                json.dumps(snapshot),
            )
            result = historical.run_replay(
                root,
                day="2026-09-30",
            )
            self.assertEqual(result["schema_rows_checked"], 1)
            self.assertEqual(result["formula_rows_checked"], 0)
            self.assertFalse(result["formula_replay_available"])
            self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
