import json
import tempfile
import unittest
from pathlib import Path

from runtime.lof.shadow_validation import run_once


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class ShadowValidationTests(unittest.TestCase):
    def test_available_shadow_resolves_against_official_nav_truth(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            data = Path(root)
            _write(
                data / "snapshots" / "runtime-20261009T210000.json",
                {
                    "snapshot_id": "runtime-20261009T210000",
                    "generated_at": "2026-10-09T21:00:00+08:00",
                    "rows": [
                        {
                            "code": "164814",
                            "official_nav": 1.01,
                            "official_nav_date": "2026-10-09",
                            "official_nav_source": "SZSE_OFFICIAL",
                        }
                    ],
                },
            )
            _write(
                data / "r2c_risk_overlay_snapshots" / "r2c-risk-overlay-20261009T143100.json",
                {
                    "snapshot_id": "r2c-risk-overlay-20261009T143100",
                    "generated_at": "2026-10-09T14:31:00+08:00",
                    "rows": [
                        {
                            "fund_code": "164814",
                            "fund_name": "工银双债LOF",
                            "method": "RISK_ASSET_OVERLAY",
                            "status": "AVAILABLE",
                            "shadow_estimated_nav": 1.02,
                        }
                    ],
                },
            )

            result = run_once(data_root=data, state_root=state)
            ledger = json.loads(
                Path(state, "shadow_validation_ledger.json").read_text()
            )

        self.assertEqual(result["observation_count"], 1)
        self.assertEqual(result["evaluated_count"], 1)
        observation = next(iter(ledger["observations"].values()))
        self.assertAlmostEqual(observation["error_pct"], (1.02 / 1.01 - 1) * 100)
        summary = next(iter(ledger["summaries"].values()))
        self.assertEqual(summary["evaluated_day_count"], 1)

    def test_unavailable_shadow_is_not_recorded(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            data = Path(root)
            _write(
                data / "r2a_shadow_snapshots" / "r2a-shadow-20261009T140000.json",
                {
                    "snapshot_id": "r2a-shadow-20261009T140000",
                    "generated_at": "2026-10-09T14:00:00+08:00",
                    "rows": [
                        {
                            "fund_code": "501219",
                            "status": "UNAVAILABLE",
                            "shadow_estimated_nav": None,
                        }
                    ],
                },
            )
            result = run_once(data_root=data, state_root=state)

        self.assertEqual(result["observation_count"], 0)

    def test_same_half_hour_keeps_latest_sample(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            data = Path(root)
            for stamp, value in [("140100", 1.01), ("142900", 1.02)]:
                _write(
                    data / "r2c_live_overlay" / "snapshots" / f"r2c-live-overlay-20261009T{stamp}.json",
                    {
                        "snapshot_id": f"r2c-live-overlay-20261009T{stamp}",
                        "generated_at": f"2026-10-09T{stamp[:2]}:{stamp[2:4]}:00+08:00",
                        "rows": [
                            {
                                "fund_code": "164814",
                                "status": "AVAILABLE",
                                "method": "LIVE_EQUITY_CONVERTIBLE_BASKET",
                                "shadow_estimated_nav": value,
                            }
                        ],
                    },
                )
            run_once(data_root=data, state_root=state)
            ledger = json.loads(
                Path(state, "shadow_validation_ledger.json").read_text()
            )

        self.assertEqual(len(ledger["observations"]), 1)
        observation = next(iter(ledger["observations"].values()))
        self.assertEqual(observation["estimated_nav"], 1.02)
        self.assertEqual(observation["sample_bin"], "14:00")

    def test_india_shadow_tracks_both_variants(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            data = Path(root)
            _write(
                data / "r4_india_shadow" / "snapshots" / "r4-india-shadow-20261009T140000.json",
                {
                    "snapshot_id": "r4-india-shadow-20261009T140000",
                    "generated_at": "2026-10-09T14:00:00+08:00",
                    "rows": [
                        {
                            "fund_code": "164824",
                            "status": "AVAILABLE",
                            "shadow_estimated_nav_sensex": 1.20,
                            "shadow_estimated_nav_sensex_inr_cny": 1.21,
                        }
                    ],
                },
            )
            result = run_once(data_root=data, state_root=state)
            ledger = json.loads(
                Path(state, "shadow_validation_ledger.json").read_text()
            )

        self.assertEqual(result["observation_count"], 2)
        variants = {row["variant"] for row in ledger["observations"].values()}
        self.assertEqual(variants, {"SENSEX", "SENSEX_INR_CNY"})

    def test_second_run_is_idempotent(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as state:
            data = Path(root)
            _write(
                data / "r2a_shadow_snapshots" / "r2a-shadow-20261009T140000.json",
                {
                    "snapshot_id": "r2a-shadow-20261009T140000",
                    "generated_at": "2026-10-09T14:00:00+08:00",
                    "rows": [
                        {
                            "fund_code": "501219",
                            "status": "AVAILABLE",
                            "method": "DISCLOSED_HOLDINGS_BASKET",
                            "shadow_estimated_nav": 1.0,
                        }
                    ],
                },
            )
            first = run_once(data_root=data, state_root=state)
            second = run_once(data_root=data, state_root=state)

        self.assertEqual(first["observation_count"], 1)
        self.assertEqual(second["observation_count"], 1)
        self.assertEqual(second["observation_updates"], 0)


if __name__ == "__main__":
    unittest.main()
