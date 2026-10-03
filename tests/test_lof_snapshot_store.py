from __future__ import annotations

import gzip
import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime.lof.snapshot_store import (
    LofSnapshotStore,
    snapshot_from_json,
    snapshot_to_json,
)


TZ = ZoneInfo("Asia/Shanghai")


class LofSnapshotStoreTest(unittest.TestCase):
    def test_round_trip_and_latest_pointer(self) -> None:
        snapshot = {
            "snapshot_id": "lof-test",
            "generated_at": datetime(2026, 9, 29, 13, 30, tzinfo=TZ),
            "universe_count": 1,
            "rows": [
                {
                    "code": "161128",
                    "price": Decimal("7.325"),
                    "official_nav_date": date(2026, 9, 24),
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            store = LofSnapshotStore(td)
            archive = store.persist(snapshot)
            self.assertTrue(archive.exists())
            self.assertTrue(archive.name.endswith(".json.gz"))
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                archived = snapshot_from_json(handle.read())
            self.assertEqual(archived["snapshot_id"], "lof-test")
            self.assertTrue((Path(td) / "latest_market_snapshot.json").exists())

            loaded = store.load_latest()
            assert loaded is not None
            self.assertEqual(loaded["snapshot_id"], "lof-test")
            self.assertEqual(loaded["rows"][0]["price"], 7.325)
            self.assertEqual(
                loaded["rows"][0]["official_nav_date"],
                "2026-09-24",
            )

    def test_last_estimate_survives_unavailable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LofSnapshotStore(td)
            first = {
                "snapshot_id": "s1",
                "generated_at": "2026-09-30T14:59:00+08:00",
                "rows": [
                    {
                        "code": "160924",
                        "name": "恒生指数LOF",
                        "price": 0.969,
                        "quote_time": "2026-09-30T14:58:48+08:00",
                        "estimated_nav": 0.9708,
                        "estimated_nav_time": "2026-09-30T14:58:48+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "MULTIDAY_PROXY_FX_BRIDGE",
                        "estimated_nav_quality": "LOW",
                        "estimated_premium_rate": -0.19,
                    }
                ],
            }
            store.persist(first)
            second = {
                "snapshot_id": "s2",
                "generated_at": "2026-10-03T10:00:00+08:00",
                "rows": [
                    {
                        "code": "160924",
                        "name": "恒生指数LOF",
                        "estimated_nav": None,
                        "estimated_nav_status": "UNAVAILABLE",
                    }
                ],
            }
            store.persist(second)
            enriched = store.enrich_with_last_estimates(second)
            row = enriched["rows"][0]
            self.assertAlmostEqual(row["last_estimated_nav"], 0.9708)
            self.assertEqual(
                row["last_estimated_nav_time"],
                "2026-09-30T14:58:48+08:00",
            )
            self.assertEqual(
                row["last_estimated_nav_status"],
                "AVAILABLE",
            )

    def test_stale_estimate_does_not_replace_last_reliable_estimate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LofSnapshotStore(td)
            available = {
                "snapshot_id": "s0",
                "generated_at": "2026-09-30T14:59:00+08:00",
                "rows": [
                    {
                        "code": "160140",
                        "name": "美国REIT精选LOF",
                        "price": 1.35,
                        "quote_time": "2026-09-30T14:58:48+08:00",
                        "estimated_nav": 1.34,
                        "estimated_nav_time": "2026-09-30T14:58:48+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "US_LAST_CLOSE_FX_BRIDGE",
                        "estimated_nav_quality": "LOW",
                        "estimated_premium_rate": 0.75,
                    }
                ],
            }
            store.persist(available)
            stale = {
                "snapshot_id": "s1",
                "generated_at": "2026-10-03T10:00:00+08:00",
                "rows": [
                    {
                        "code": "160140",
                        "name": "美国REIT精选LOF",
                        "price": 1.36,
                        "quote_time": "2026-09-30T16:14:27+08:00",
                        "estimated_nav": 1.36,
                        "estimated_nav_time": "2026-09-30T16:14:27+08:00",
                        "estimated_nav_status": "STALE",
                        "estimated_nav_method": "US_LAST_CLOSE_FX_BRIDGE",
                        "estimated_nav_quality": "LOW",
                        "estimated_premium_rate": None,
                    }
                ],
            }
            store.persist(stale)
            row = store.enrich_with_last_estimates(stale)["rows"][0]
            self.assertEqual(row["last_estimated_nav_status"], "AVAILABLE")
            self.assertAlmostEqual(row["last_estimated_nav"], 1.34)
            self.assertEqual(
                row["last_estimated_nav_time"],
                "2026-09-30T14:58:48+08:00",
            )

    def test_component_holiday_recompute_is_not_last_reliable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LofSnapshotStore(td)
            valid = {
                "snapshot_id": "s-valid",
                "generated_at": "2026-09-30T15:00:00+08:00",
                "rows": [
                    {
                        "code": "501005",
                        "name": "精准医疗LOF",
                        "price": 1.10,
                        "quote_time": "2026-09-30T14:59:50+08:00",
                        "estimated_nav": 1.09,
                        "estimated_nav_time": "2026-09-30T14:59:50+08:00",
                        "estimated_nav_proxy_time": "2026-09-30T14:59:50+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
                        "estimated_nav_quality": "MEDIUM",
                        "estimated_premium_rate": 0.91,
                    }
                ],
            }
            bad_holiday = {
                "snapshot_id": "s-bad",
                "generated_at": "2026-10-03T10:00:00+08:00",
                "rows": [
                    {
                        "code": "501005",
                        "name": "精准医疗LOF",
                        "price": 1.10,
                        "quote_time": "2026-09-30T16:14:23+08:00",
                        "estimated_nav": 1.11,
                        "estimated_nav_time": "2026-10-03T10:00:00+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
                        "estimated_nav_quality": "MEDIUM",
                        "estimated_premium_rate": -0.90,
                    }
                ],
            }
            store.persist(valid)
            store.persist(bad_holiday)
            row = store.enrich_with_last_estimates(bad_holiday)["rows"][0]
            self.assertAlmostEqual(row["last_estimated_nav"], 1.09)
            self.assertEqual(
                row["last_estimated_nav_time"],
                "2026-09-30T14:59:50+08:00",
            )

    def test_component_post_close_recompute_does_not_replace_last_reliable(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LofSnapshotStore(td)
            valid = {
                "snapshot_id": "s-valid",
                "generated_at": "2026-09-30T14:59:30+08:00",
                "rows": [
                    {
                        "code": "501089",
                        "name": "消费红利增强LOF",
                        "price": 1.05,
                        "quote_time": "2026-09-30T14:59:20+08:00",
                        "estimated_nav": 1.04,
                        "estimated_nav_time": "2026-09-30T14:59:20+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
                        "estimated_nav_quality": "MEDIUM",
                        "estimated_premium_rate": 0.96,
                    }
                ],
            }
            post_close = {
                "snapshot_id": "s-post",
                "generated_at": "2026-09-30T23:55:10+08:00",
                "rows": [
                    {
                        "code": "501089",
                        "name": "消费红利增强LOF",
                        "price": 1.05,
                        "quote_time": "2026-09-30T16:14:20+08:00",
                        "estimated_nav": 1.03,
                        "estimated_nav_time": "2026-09-30T23:55:10+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
                        "estimated_nav_quality": "MEDIUM",
                        "estimated_premium_rate": 1.94,
                    }
                ],
            }
            store.persist(valid)
            store.persist(post_close)
            row = store.enrich_with_last_estimates(post_close)["rows"][0]
            self.assertAlmostEqual(row["last_estimated_nav"], 1.04)
            self.assertEqual(
                row["last_estimated_nav_time"],
                "2026-09-30T14:59:20+08:00",
            )

    def test_rebuild_last_estimates_reads_compressed_archives(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            store = LofSnapshotStore(td)
            snapshot = {
                "snapshot_id": "runtime-20260930T145900",
                "generated_at": "2026-09-30T14:59:00+08:00",
                "rows": [
                    {
                        "code": "501016",
                        "name": "券商基金LOF",
                        "price": 1.01,
                        "quote_time": "2026-09-30T14:58:50+08:00",
                        "estimated_nav": 1.0,
                        "estimated_nav_time": "2026-09-30T14:58:50+08:00",
                        "estimated_nav_status": "AVAILABLE",
                        "estimated_nav_method": "INDEX_PROXY_PREV_CLOSE",
                        "estimated_premium_rate": 1.0,
                    }
                ],
            }
            archive = store.persist(snapshot)
            self.assertTrue(archive.name.endswith(".json.gz"))
            store.last_estimates_path.unlink()
            rebuilt = store.rebuild_last_estimates()
            self.assertEqual(len(rebuilt["rows"]), 1)
            self.assertAlmostEqual(
                rebuilt["rows"]["501016"]["estimated_nav"],
                1.0,
            )

    def test_snapshot_id_is_required(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                LofSnapshotStore(td).persist({})

    def test_non_object_json_rejected(self) -> None:
        with self.assertRaises(ValueError):
            snapshot_from_json("[]")

    def test_decimal_serializes_as_json_number(self) -> None:
        text = snapshot_to_json(
            {"snapshot_id": "x", "value": Decimal("1.25")}
        )
        self.assertIn('"value":1.25', text)


if __name__ == "__main__":
    unittest.main()
