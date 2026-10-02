import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime.lof.shadow_registry import load_shadow_registry


TZ = ZoneInfo("Asia/Shanghai")


class ShadowRegistryTests(unittest.TestCase):
    def test_layers_main_t1_and_shadow_without_promoting_shadow(self):
        snapshot = {
            "snapshot_id": "main-1",
            "rows": [
                {
                    "code": "100001",
                    "name": "Main",
                    "estimated_nav_status": "UNAVAILABLE",
                    "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                },
                {
                    "code": "160916",
                    "name": "CashShadow",
                    "estimated_nav_status": "UNAVAILABLE",
                },
                {
                    "code": "161119",
                    "name": "BondLike",
                    "estimated_nav_status": "UNAVAILABLE",
                },
                {
                    "code": "999999",
                    "name": "Unknown",
                    "estimated_nav_status": "UNAVAILABLE",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            (base / "r2b2_cash_shadow").mkdir()
            (base / "r2b2_cash_shadow" / "r2b2_cash_shadow.json").write_text(
                json.dumps(
                    {
                        "snapshot_id": "shadow-1",
                        "generated_at": "2026-10-03T09:30:00+08:00",
                        "rows": [
                            {
                                "fund_code": "160916",
                                "status": "AVAILABLE",
                                "method": "CASH",
                                "shadow_estimated_nav": 1.1,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (base / "r2c_nav_band_profiles.json").write_text(
                json.dumps(
                    {
                        "version": "x",
                        "generated_at": "2026-10-03T08:00:00+08:00",
                        "rows": {
                            "161119": {
                                "mae_abs_return": 0.05,
                                "up95": 0.1,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = load_shadow_registry(
                root,
                main_snapshot=snapshot,
                now=datetime(2026, 10, 3, 9, 31, tzinfo=TZ),
            )

        self.assertEqual(
            result["rows"]["100001"]["display_state"],
            "MAIN_ESTIMATE",
        )
        self.assertTrue(
            result["rows"]["100001"]["main_estimate_covered"]
        )
        self.assertFalse(
            result["rows"]["100001"]["main_estimate_available_now"]
        )
        self.assertEqual(
            result["rows"]["160916"]["display_state"],
            "ACTIVE_SHADOW",
        )
        self.assertFalse(
            result["rows"]["160916"]["main_estimate_covered"]
        )
        self.assertEqual(
            result["rows"]["161119"]["display_state"],
            "T1_PROFILE",
        )
        self.assertEqual(
            result["rows"]["999999"]["display_state"],
            "UNRESOLVED",
        )
        self.assertEqual(
            result["summary"]["active_shadow_fund_count"],
            1,
        )

    def test_validation_metrics_are_compact(self):
        snapshot = {
            "snapshot_id": "main-1",
            "rows": [
                {
                    "code": "501025",
                    "name": "HK Bank",
                    "estimated_nav_status": "UNAVAILABLE",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            d = base / "r5_cross_border_501025_shadow"
            d.mkdir()
            (d / "r5_cross_border_501025_shadow.json").write_text(
                json.dumps(
                    {
                        "snapshot_id": "s1",
                        "generated_at": "2026-10-03T09:30:00+08:00",
                        "rows": [
                            {
                                "fund_code": "501025",
                                "status": "UNAVAILABLE",
                                "method": "INDEX",
                                "error": "NAV_NOT_T1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            v = base / "shadow_validation"
            v.mkdir()
            (v / "shadow_validation_ledger.json").write_text(
                json.dumps(
                    {
                        "updated_at": "2026-10-03T09:00:00+08:00",
                        "summaries": {
                            "x": {
                                "model_id": "INDEX",
                                "fund_code": "501025",
                                "fund_name": "HK Bank",
                                "observation_count": 10,
                                "evaluated_count": 8,
                                "pending_count": 2,
                                "mae_pct": 0.12,
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            result = load_shadow_registry(
                root,
                main_snapshot=snapshot,
                now=datetime(2026, 10, 3, 9, 31, tzinfo=TZ),
            )

        row = result["rows"]["501025"]
        self.assertEqual(row["shadow_model_count"], 1)
        self.assertEqual(row["validation_evaluated_count"], 8)
        self.assertEqual(row["validation_best_mae_pct"], 0.12)

    def test_researched_deferred_is_not_unresolved(self):
        snapshot = {
            "snapshot_id": "main-1",
            "rows": [
                {
                    "code": "160220",
                    "name": "Deferred",
                    "resolver_class": "R2_DOMESTIC_OTHER",
                    "lof_type": "MIXED",
                    "estimated_nav_status": "UNAVAILABLE",
                    "estimated_nav_method": "UNAVAILABLE",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as root:
            base = Path(root)
            knowledge = "k1"
            (base / "deployment_manifest.json").write_text(
                json.dumps({"knowledge_commit_sha": knowledge}),
                encoding="utf-8",
            )
            target = (
                base
                / "knowledge_snapshots"
                / knowledge
                / "files"
                / "05 套利研究"
                / "LOF机会发现"
                / "02_数据与监控"
            )
            target.mkdir(parents=True)
            (
                target
                / "LOF-Research-Disposition-Registry-V0.1.json"
            ).write_text(
                json.dumps(
                    {
                        "version": "x",
                        "entries": {
                            "160220": {
                                "status": "RESEARCHED_DEFERRED",
                                "reason_code": "MODEL_REJECTED",
                            }
                        },
                        "rules": [],
                    }
                ),
                encoding="utf-8",
            )
            result = load_shadow_registry(
                root,
                main_snapshot=snapshot,
                now=datetime(2026, 10, 3, 9, 31, tzinfo=TZ),
            )

        self.assertEqual(
            result["rows"]["160220"]["display_state"],
            "RESEARCHED_DEFERRED",
        )
        self.assertEqual(
            result["summary"]["researched_deferred_count"],
            1,
        )
        self.assertEqual(result["summary"]["unresolved_count"], 0)


if __name__ == "__main__":
    unittest.main()
