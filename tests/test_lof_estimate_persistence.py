from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from runtime.lof.estimate_model_registry import (
    REGISTRY_VERSION,
    estimate_model_id,
    estimate_model_version,
    registry_snapshot,
)
from runtime.lof.estimate_persistence import (
    LEGACY_UNVERIFIED,
    QUARANTINED,
    VALID_AUDITED_BASELINE,
    audit_estimate_history,
    history_validation_eligible,
)
from runtime.lof.snapshot_retention import plan_snapshot_retention


TZ = ZoneInfo("Asia/Shanghai")


class EstimateModelRegistryTest(unittest.TestCase):
    def test_registered_model_identity_is_stable(self):
        self.assertEqual(
            estimate_model_id("CSI_COMPONENT_WEIGHT_PREV_CLOSE"),
            "R1_CSI_COMPONENT",
        )
        self.assertEqual(
            estimate_model_version("CSI_COMPONENT_WEIGHT_PREV_CLOSE"),
            "R1_CSI_COMPONENT_V2",
        )
        snapshot = registry_snapshot()
        self.assertEqual(snapshot["registry_version"], REGISTRY_VERSION)
        self.assertGreaterEqual(snapshot["model_count"], 12)


class EstimatePersistenceAuditTest(unittest.TestCase):
    def _write_history(self, root: Path, day: str, rows: dict) -> None:
        directory = root / "estimate_history"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{day}.json").write_text(
            json.dumps(
                {
                    "version": "LOF_ESTIMATE_HISTORY_V1",
                    "date": day,
                    "updated_at": f"{day}T15:10:00+08:00",
                    "rows": rows,
                }
            ),
            encoding="utf-8",
        )

    def test_audit_separates_valid_legacy_and_known_dirty_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self._write_history(
                root,
                "2026-09-30",
                {
                    "501005": {
                        "code": "501005",
                        "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
                        "estimated_nav": 1.01,
                        "estimated_nav_time": "2026-09-30T15:04:40+08:00",
                        "quote_time": "2026-09-30T15:04:22+08:00",
                    },
                    "501016": {
                        "code": "501016",
                        "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                        "estimated_nav": 1.02,
                        "estimated_nav_time": "2026-09-30T15:00:00+08:00",
                        "quote_time": "2026-09-30T15:00:00+08:00",
                    },
                },
            )
            self._write_history(
                root,
                "2026-10-01",
                {
                    "161128": {
                        "code": "161128",
                        "estimated_nav_method": "MULTIDAY_PROXY_FX_BRIDGE",
                        "estimated_nav": 2.0,
                        "estimated_nav_time": "2026-10-01T15:10:00+08:00",
                        "quote_time": "2026-09-30T15:00:00+08:00",
                    }
                },
            )
            self._write_history(
                root,
                "2026-10-03",
                {
                    "501005": {
                        "code": "501005",
                        "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
                        "estimated_nav": 1.03,
                        "estimated_nav_time": "2026-10-03T15:21:30+08:00",
                        "quote_time": "2026-09-30T16:14:22+08:00",
                    }
                },
            )

            result = audit_estimate_history(
                root,
                apply=True,
                now=datetime(2026, 10, 3, 21, 0, tzinfo=TZ),
            )

            self.assertEqual(result["quarantined_row_count"], 1)
            self.assertEqual(result["validation_eligible_count"], 2)
            self.assertEqual(result["legacy_unverified_count"], 1)

            sep30 = json.loads(
                (root / "estimate_history/2026-09-30.json").read_text()
            )
            self.assertEqual(
                sep30["rows"]["501005"]["validity_status"],
                VALID_AUDITED_BASELINE,
            )
            self.assertTrue(
                history_validation_eligible(
                    sep30["rows"]["501005"],
                    truth_date="2026-09-30",
                )
            )
            self.assertEqual(
                sep30["rows"]["501005"]["estimated_model_version"],
                "R1_CSI_COMPONENT_V2",
            )

            oct1 = json.loads(
                (root / "estimate_history/2026-10-01.json").read_text()
            )
            self.assertEqual(
                oct1["rows"]["161128"]["validity_status"],
                LEGACY_UNVERIFIED,
            )
            self.assertFalse(
                history_validation_eligible(
                    oct1["rows"]["161128"],
                    truth_date="2026-10-01",
                )
            )

            oct3 = json.loads(
                (root / "estimate_history/2026-10-03.json").read_text()
            )
            self.assertEqual(oct3["rows"], {})
            quarantine = json.loads(
                (
                    root
                    / "estimate_history_quarantine/2026-10-03.json"
                ).read_text()
            )
            self.assertEqual(
                quarantine["rows"]["501005"]["validity_status"],
                QUARANTINED,
            )
            self.assertIn(
                "OUTSIDE_DOMESTIC_REFRESH_WINDOW",
                quarantine["rows"]["501005"]["validity_reason"],
            )
            self.assertTrue((root / "estimate_model_registry.json").exists())
            self.assertTrue((root / "estimate_history_manifest.json").exists())


class SnapshotRetentionTest(unittest.TestCase):
    def _touch_snapshot(self, root: Path, stamp: str) -> Path:
        directory = root / "snapshots"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"runtime-{stamp}.json"
        path.write_text("{}", encoding="utf-8")
        return path

    def test_retention_keeps_full_recent_five_midterm_one_longterm_and_pins(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)

            # Recent day: keep all.
            recent = [
                self._touch_snapshot(root, "20261002T093000"),
                self._touch_snapshot(root, "20261002T100000"),
                self._touch_snapshot(root, "20261002T150000"),
            ]

            # Midterm day: downsample to 5 checkpoints.
            midterm = []
            for hhmm in (
                "093000",
                "100000",
                "103000",
                "110000",
                "113000",
                "140000",
                "143000",
                "145000",
                "150000",
                "153000",
            ):
                midterm.append(
                    self._touch_snapshot(root, f"20260920T{hhmm}")
                )

            # Long-term day: keep one close checkpoint, plus a pin.
            longterm_close = self._touch_snapshot(root, "20260701T150000")
            pinned = self._touch_snapshot(root, "20260701T100000")
            self._touch_snapshot(root, "20260701T110000")
            (root / "snapshot_retention_pins.json").write_text(
                json.dumps({"snapshot_ids": [pinned.stem]}),
                encoding="utf-8",
            )

            plan = plan_snapshot_retention(
                root,
                as_of=date(2026, 10, 3),
            )
            keep = {Path(value).name for value in plan["keep_paths"]}
            delete = {Path(value).name for value in plan["delete_paths"]}

            for path in recent:
                self.assertIn(path.name, keep)
            self.assertEqual(
                plan["days"]["2026-09-20"]["keep_count"],
                5,
            )
            self.assertIn(longterm_close.name, keep)
            self.assertIn(pinned.name, keep)
            self.assertEqual(
                plan["days"]["2026-07-01"]["keep_count"],
                2,
            )
            self.assertGreater(len(delete), 0)


if __name__ == "__main__":
    unittest.main()
