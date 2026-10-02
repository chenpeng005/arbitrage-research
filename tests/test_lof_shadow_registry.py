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
                    "estimated_nav_status": "AVAILABLE",
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
        self.assertEqual(
            result["rows"]["160916"]["display_state"],
            "ACTIVE_SHADOW",
        )
        self.assertFalse(
            result["rows"]["160916"]["main_estimate_available"]
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


if __name__ == "__main__":
    unittest.main()
