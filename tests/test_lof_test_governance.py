from __future__ import annotations

from datetime import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


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
            self.assertEqual(result["rows_checked"], 1)
            self.assertEqual(result["failed"], 0)


if __name__ == "__main__":
    unittest.main()
