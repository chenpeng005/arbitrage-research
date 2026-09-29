from __future__ import annotations

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
            self.assertTrue((Path(td) / "latest_market_snapshot.json").exists())

            loaded = store.load_latest()
            assert loaded is not None
            self.assertEqual(loaded["snapshot_id"], "lof-test")
            self.assertEqual(loaded["rows"][0]["price"], 7.325)
            self.assertEqual(
                loaded["rows"][0]["official_nav_date"],
                "2026-09-24",
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
