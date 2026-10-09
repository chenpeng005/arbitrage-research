from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.opportunity.daily_notification_snapshot import (
    capture_daily_notification_snapshot,
    list_daily_notification_snapshots,
    load_daily_notification_snapshot,
)


class DailyNotificationSnapshotV1Test(unittest.TestCase):
    def test_capture_list_and_load_are_idempotent_by_date(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "state").mkdir(parents=True)
            (root / "state" / "incremental_runtime.sqlite").write_bytes(b"stub")
            feed = {
                "status": "PASS",
                "focus_count": 2,
                "other_count": 3,
                "items": [{"notification_group_id": "NTF_A"}],
                "other_items": [{"group_key": "CHANGE_A"}],
            }
            records = {
                "market_cutoff": "2026-10-08",
                "bond_count": 167,
                "keep_path_count": 183,
            }
            with patch(
                "runtime.opportunity.daily_notification_snapshot.build_notification_feed",
                return_value=feed,
            ), patch(
                "runtime.opportunity.daily_notification_snapshot.load_opportunity_records",
                return_value=records,
            ):
                first = capture_daily_notification_snapshot(
                    data_root=root,
                    snapshot_date="2026-10-08",
                    update_status="PASS",
                )
                second = capture_daily_notification_snapshot(
                    data_root=root,
                    snapshot_date="2026-10-08",
                    update_status="RECOVERED",
                )

            self.assertEqual(first["focus_count"], 2)
            self.assertEqual(second["update_status"], "RECOVERED")
            self.assertEqual(second["market_cutoff"], "2026-10-08")
            self.assertEqual(second["opportunity_bond_count"], 167)
            files = list((root / "notification_snapshots").glob("*.json"))
            self.assertEqual(len(files), 1)

            history = list_daily_notification_snapshots(data_root=root)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["snapshot_date"], "2026-10-08")
            self.assertEqual(history[0]["focus_count"], 2)
            loaded = load_daily_notification_snapshot(
                data_root=root,
                snapshot_date="2026-10-08",
            )
            self.assertEqual(loaded["feed"], feed)

    def test_bad_date_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                load_daily_notification_snapshot(
                    data_root=Path(td),
                    snapshot_date="../../etc/passwd",
                )


if __name__ == "__main__":
    unittest.main()
