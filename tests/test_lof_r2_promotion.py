from __future__ import annotations

from datetime import date, datetime
import json
from pathlib import Path
import tempfile
import unittest
from zoneinfo import ZoneInfo

from runtime.lof.r2_promotion import load_promoted_r2_results


TZ = ZoneInfo("Asia/Shanghai")


class R2PromotionTest(unittest.TestCase):
    def _write(self, root: Path, rel: str, payload: dict) -> None:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

    def test_promotes_only_explicit_evidence_cohort(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)
            anchor = date(2026, 9, 30)

            self._write(
                root,
                "r2a_shadow_snapshot.json",
                {
                    "generated_at": now.isoformat(),
                    "rows": [
                        {
                            "fund_code": "501219",
                            "method": "DISCLOSED_HOLDINGS_BASKET",
                            "status": "AVAILABLE",
                            "quality_candidate": "MEDIUM",
                            "shadow_estimated_nav": 1.23,
                            "estimated_return": 0.01,
                            "official_nav_date": anchor.isoformat(),
                            "quote_time_min": now.isoformat(),
                            "disclosed_weight": 0.94,
                        },
                        {
                            "fund_code": "501201",
                            "method": "DISCLOSED_HOLDINGS_BASKET",
                            "status": "AVAILABLE",
                            "quality_candidate": "LOW",
                            "shadow_estimated_nav": 1.11,
                            "official_nav_date": anchor.isoformat(),
                            "quote_time_min": now.isoformat(),
                        },
                    ],
                },
            )
            self._write(
                root,
                "r2b2_cash_shadow/r2b2_cash_shadow.json",
                {
                    "generated_at": now.isoformat(),
                    "rows": [
                        {
                            "fund_code": "160916",
                            "method": "R2B2_CASH_HEAVY_HOLDINGS_BASKET",
                            "status": "AVAILABLE",
                            "quality_candidate": "MEDIUM",
                            "shadow_estimated_nav": 4.7,
                            "estimated_return": 0.005,
                            "official_nav_date": anchor.isoformat(),
                            "quote_time_min": now.isoformat(),
                            "disclosed_stock_weight": 0.73,
                            "backtest": {
                                "mae_pct": 0.162,
                                "p90_pct": 0.339,
                                "corr": 0.982,
                            },
                        }
                    ],
                },
            )
            self._write(
                root,
                "r2c_risk_overlay_shadow.json",
                {
                    "generated_at": now.isoformat(),
                    "rows": [
                        {
                            "fund_code": "160621",
                            "method": "RISK_ASSET_OVERLAY",
                            "status": "AVAILABLE",
                            "quality_candidate": "MEDIUM",
                            "shadow_estimated_nav": 1.4,
                            "estimated_return": 0.002,
                            "official_nav_date": anchor.isoformat(),
                            "quote_time_min": now.isoformat(),
                            "total_risk_weight": 0.15,
                        }
                    ],
                },
            )

            result = load_promoted_r2_results(
                data_root=root,
                expected_anchor_date=anchor,
                as_of=now,
            )
            self.assertEqual(set(result), {"501219", "160916", "160621"})
            self.assertEqual(result["501219"].estimated_nav_status, "AVAILABLE")
            self.assertEqual(
                result["160916"].resolver_method,
                "R2B2_CASH_HEAVY_HOLDINGS_BASKET",
            )
            self.assertEqual(result["160621"].estimated_nav_quality, "MEDIUM")

    def test_rejects_wrong_anchor_and_downgrades_old_quote_to_stale(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            now = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)
            old = datetime(2026, 10, 9, 9, 50, tzinfo=TZ)
            anchor = date(2026, 9, 30)

            self._write(
                root,
                "r2a_shadow_snapshot.json",
                {
                    "generated_at": now.isoformat(),
                    "rows": [
                        {
                            "fund_code": "501219",
                            "method": "DISCLOSED_HOLDINGS_BASKET",
                            "status": "AVAILABLE",
                            "quality_candidate": "MEDIUM",
                            "shadow_estimated_nav": 1.23,
                            "official_nav_date": anchor.isoformat(),
                            "quote_time_min": old.isoformat(),
                        },
                        {
                            "fund_code": "160133",
                            "method": "DISCLOSED_HOLDINGS_BASKET",
                            "status": "AVAILABLE",
                            "quality_candidate": "MEDIUM",
                            "shadow_estimated_nav": 4.1,
                            "official_nav_date": "2026-09-29",
                            "quote_time_min": now.isoformat(),
                        },
                    ],
                },
            )

            result = load_promoted_r2_results(
                data_root=root,
                expected_anchor_date=anchor,
                as_of=now,
                max_proxy_age_seconds=120,
            )
            self.assertEqual(set(result), {"501219"})
            self.assertEqual(result["501219"].estimated_nav_status, "STALE")


if __name__ == "__main__":
    unittest.main()
