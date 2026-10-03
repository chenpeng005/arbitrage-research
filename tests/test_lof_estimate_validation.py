from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.lof.estimate_validation import (
    load_estimate_validation_summary,
    record_estimate_history,
    update_estimate_validation_ledger,
)


class LofEstimateValidationTest(unittest.TestCase):
    def test_estimate_is_validated_when_same_day_official_nav_arrives(self):
        with tempfile.TemporaryDirectory() as td:
            estimate_snapshot = {
                "snapshot_id": "s-est",
                "generated_at": "2026-09-30T15:00:00+08:00",
                "rows": [
                    {
                        "code": "501016",
                        "name": "券商基金LOF",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "estimated_nav": 1.005,
                        "estimated_nav_time": "2026-09-30T14:59:50+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                        "estimated_nav_quality": "MEDIUM",
                        "estimated_nav_proxy": "399707",
                        "estimated_nav_proxy_time": "2026-09-30T14:59:50+08:00",
                        "estimated_nav_proxy_return": 0.01,
                        "price": 1.01,
                        "quote_time": "2026-09-30T14:59:48+08:00",
                    }
                ],
            }
            record_estimate_history(td, estimate_snapshot)

            truth_snapshot = {
                "snapshot_id": "s-truth",
                "generated_at": "2026-10-01T09:00:00+08:00",
                "rows": [
                    {
                        "code": "501016",
                        "name": "券商基金LOF",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "official_nav": 1.0,
                        "official_nav_date": "2026-09-30",
                    }
                ],
            }
            ledger = update_estimate_validation_ledger(td, truth_snapshot)
            self.assertEqual(ledger["summary"]["observation_count"], 1)
            row = ledger["rows"]["501016"]
            self.assertEqual(row["sample_count"], 1)
            self.assertAlmostEqual(row["mae_pct"], 0.5, places=6)
            self.assertAlmostEqual(row["bias_pct"], 0.5, places=6)
            self.assertEqual(row["last_truth_date"], "2026-09-30")

            summary = load_estimate_validation_summary(td)
            self.assertEqual(summary["methods"]["INDEX_PROXY_PREV_CLOSE"]["sample_count"], 1)

    def test_stale_estimate_is_not_written_to_daily_history(self):
        with tempfile.TemporaryDirectory() as td:
            snapshot = {
                "snapshot_id": "s-stale",
                "generated_at": "2026-10-03T10:00:00+08:00",
                "rows": [
                    {
                        "code": "501016",
                        "estimated_nav": 1.005,
                        "estimated_nav_time": "2026-09-30T15:00:00+08:00",
                        "estimated_nav_status": "STALE",
                    }
                ],
            }
            result = record_estimate_history(td, snapshot)
            self.assertEqual(result["days"], {})
            self.assertFalse((Path(td) / "estimate_history" / "2026-09-30.json").exists())


if __name__ == "__main__":
    unittest.main()
