from __future__ import annotations

import json
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
            method = summary["methods"]["INDEX_PROXY_PREV_CLOSE"]
            self.assertEqual(method["sample_count"], 1)
            self.assertEqual(method["current_model_version"], "R1_INDEX_PROXY_V1")
            self.assertEqual(method["current_version"]["sample_count"], 1)
            self.assertEqual(method["windows"]["1"]["sample_count"], 1)
            self.assertEqual(method["windows"]["3"]["sample_count"], 1)
            self.assertAlmostEqual(
                method["current_version"]["max_abs_error_pct"],
                0.5,
                places=6,
            )

    def test_audited_sep30_legacy_observation_seeds_current_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = {
                "version": "LOF_ESTIMATE_VALIDATION_V1",
                "truth_signature": "old",
                "updated_at": "2026-10-01T00:00:00+08:00",
                "observations": {
                    "501016|2026-09-30": {
                        "code": "501016",
                        "name": "券商基金LOF",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "method": "INDEX_PROXY_PREV_CLOSE",
                        "truth_date": "2026-09-30",
                        "official_nav": 1.0,
                        "estimated_nav": 1.001,
                        "estimated_nav_time": "2026-09-30T15:00:00+08:00",
                        "error_pct": 0.1,
                        "abs_error_pct": 0.1,
                    }
                },
                "rows": {},
                "methods": {},
                "summary": {},
            }
            (root / "estimate_validation_ledger.json").write_text(
                json.dumps(ledger),
                encoding="utf-8",
            )
            history_dir = root / "estimate_history"
            history_dir.mkdir(parents=True, exist_ok=True)
            history = {
                "version": "LOF_ESTIMATE_HISTORY_V1",
                "date": "2026-09-30",
                "updated_at": "2026-09-30T15:00:00+08:00",
                "rows": {
                    "501016": {
                        "code": "501016",
                        "name": "券商基金LOF",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "estimated_nav": 1.001,
                        "estimated_nav_time": "2026-09-30T15:00:00+08:00",
                        "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                        "estimated_nav_proxy": "399707",
                        "source_snapshot_id": "legacy-estimate",
                    }
                },
            }
            (history_dir / "2026-09-30.json").write_text(
                json.dumps(history),
                encoding="utf-8",
            )
            latest = {
                "snapshot_id": "latest",
                "generated_at": "2026-10-03T17:00:00+08:00",
                "rows": [
                    {
                        "code": "501016",
                        "name": "券商基金LOF",
                        "resolver_class": "R1_DOMESTIC_INDEX",
                        "official_nav": 1.0,
                        "official_nav_date": "2026-09-30",
                        "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                    }
                ],
            }
            (root / "latest_market_snapshot.json").write_text(
                json.dumps(latest),
                encoding="utf-8",
            )
            update_estimate_validation_ledger(td, latest)
            summary = load_estimate_validation_summary(td)
            row = summary["rows"]["501016"]
            self.assertEqual(row["current_model_version"], "R1_INDEX_PROXY_V1")
            self.assertEqual(row["current_version"]["sample_count"], 1)
            self.assertEqual(row["windows"]["1"]["sample_count"], 1)

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


    def test_current_version_has_1_3_5_10_day_windows(self):
        with tempfile.TemporaryDirectory() as td:
            errors = [0.1, -0.2, 0.3, -0.4, 0.5]
            for index, error in enumerate(errors, start=1):
                day = f"2026-09-{20 + index:02d}"
                estimated = 1.0 * (1 + error / 100)
                estimate_snapshot = {
                    "snapshot_id": f"est-{index}",
                    "generated_at": f"{day}T15:00:00+08:00",
                    "rows": [
                        {
                            "code": "501016",
                            "name": "券商基金LOF",
                            "resolver_class": "R1_DOMESTIC_INDEX",
                            "estimated_nav": estimated,
                            "estimated_nav_time": f"{day}T14:59:50+08:00",
                            "estimated_nav_status": "AVAILABLE",
                            "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                            "estimated_nav_quality": "MEDIUM",
                            "estimated_nav_proxy": "399707",
                            "estimated_nav_proxy_time": f"{day}T14:59:50+08:00",
                            "quote_time": f"{day}T14:59:48+08:00",
                        }
                    ],
                }
                record_estimate_history(td, estimate_snapshot)
                truth_snapshot = {
                    "snapshot_id": f"truth-{index}",
                    "generated_at": f"2026-09-{21 + index:02d}T09:00:00+08:00",
                    "rows": [
                        {
                            "code": "501016",
                            "name": "券商基金LOF",
                            "resolver_class": "R1_DOMESTIC_INDEX",
                            "official_nav": 1.0,
                            "official_nav_date": day,
                            "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                        }
                    ],
                }
                update_estimate_validation_ledger(td, truth_snapshot)

            summary = load_estimate_validation_summary(td)
            row = summary["rows"]["501016"]
            self.assertEqual(row["current_version"]["sample_count"], 5)
            self.assertEqual(row["windows"]["1"]["sample_count"], 1)
            self.assertEqual(row["windows"]["3"]["sample_count"], 3)
            self.assertEqual(row["windows"]["5"]["sample_count"], 5)
            self.assertEqual(row["windows"]["10"]["sample_count"], 5)
            self.assertAlmostEqual(row["windows"]["1"]["mae_pct"], 0.5, places=6)
            self.assertAlmostEqual(row["windows"]["3"]["mae_pct"], 0.4, places=6)
            self.assertAlmostEqual(row["windows"]["5"]["mae_pct"], 0.3, places=6)
            self.assertAlmostEqual(
                row["current_version"]["max_abs_error_pct"],
                0.5,
                places=6,
            )

    def test_legacy_history_is_not_counted_as_current_model_version(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            ledger = {
                "version": "LOF_ESTIMATE_VALIDATION_V1",
                "truth_signature": "old",
                "updated_at": "2026-10-01T00:00:00+08:00",
                "observations": {
                    "160621|2026-09-30": {
                        "code": "160621",
                        "name": "鹏华丰和LOF",
                        "resolver_class": "R2_DOMESTIC_OTHER",
                        "method": "RISK_ASSET_OVERLAY",
                        "truth_date": "2026-09-30",
                        "official_nav": 1.0,
                        "estimated_nav": 1.001,
                        "estimated_nav_time": "2026-09-30T15:00:00+08:00",
                        "error_pct": 0.1,
                        "abs_error_pct": 0.1,
                    }
                },
                "rows": {},
                "methods": {},
                "summary": {},
            }
            (root / "estimate_validation_ledger.json").write_text(
                json.dumps(ledger),
                encoding="utf-8",
            )
            latest = {
                "snapshot_id": "latest",
                "generated_at": "2026-10-03T17:00:00+08:00",
                "rows": [
                    {
                        "code": "160621",
                        "name": "鹏华丰和LOF",
                        "resolver_class": "R2_DOMESTIC_OTHER",
                        "estimated_nav_method": "RISK_ASSET_OVERLAY",
                    }
                ],
            }
            (root / "latest_market_snapshot.json").write_text(
                json.dumps(latest),
                encoding="utf-8",
            )
            summary = load_estimate_validation_summary(td)
            row = summary["rows"]["160621"]
            self.assertEqual(row["sample_count"], 1)
            self.assertEqual(row["current_model_version"], "R2C_RISK_OVERLAY_V1")
            self.assertEqual(row["current_version"]["sample_count"], 0)
            self.assertEqual(row["windows"]["1"]["sample_count"], 0)
            self.assertEqual(summary["summary"]["current_version_validated_fund_count"], 0)


if __name__ == "__main__":
    unittest.main()
