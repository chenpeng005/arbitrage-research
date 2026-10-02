import json
import tempfile
import unittest
from pathlib import Path

from runtime.lof.r2c_profile_summary import load_r2c_t1_profile_summary


class R2CT1ProfileSummaryTests(unittest.TestCase):
    def test_missing_profile_file_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as root:
            result = load_r2c_t1_profile_summary(root)
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertEqual(result["rows"], {})

    def test_profile_summary_drops_nav_history(self):
        with tempfile.TemporaryDirectory() as root:
            payload = {
                "version": "R2C_NAV_BAND_PROFILE_V2",
                "generated_at": "2026-10-01T20:01:18+08:00",
                "rows": {
                    "163907": {
                        "mae_abs_return": 0.0058,
                        "up95": 0.02,
                        "up99": 0.03,
                        "group": "C1_ULTRA_LOW",
                        "reliability": "MEDIUM",
                        "history_end_date": "2026-09-30",
                        "return_sample_count": 299,
                        "walk_forward_count": 179,
                        "walk_forward_exceed95": 5.0,
                        "walk_forward_exceed99": 1.7,
                        "nav_history": [{"date": "2026-09-30", "nav": 1.0}],
                    }
                },
            }
            Path(root, "r2c_nav_band_profiles.json").write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            result = load_r2c_t1_profile_summary(root)

        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["row_count"], 1)
        row = result["rows"]["163907"]
        self.assertEqual(row["mae_abs_return"], 0.0058)
        self.assertEqual(row["history_end_date"], "2026-09-30")
        self.assertNotIn("nav_history", row)


if __name__ == "__main__":
    unittest.main()
