from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.opportunity.wechat_snapshot import (
    ConvertibleWechatSnapshotStore,
    build_convertible_wechat_snapshot,
)


def _source(captured_at: str, *, rows: int = 1) -> dict:
    items = []
    for idx in range(rows):
        items.append(
            {
                "bond_code": f"12345{idx}",
                "bond_name": f"测试转债{idx}",
                "level": "IMMEDIATE",
                "created_at": captured_at,
                "changes": [
                    {
                        "change_type": "FACT_UPDATED",
                        "scope_id": "PUT",
                        "detected_at": captured_at,
                    }
                ],
                "presentation": {
                    "kind": "EVENT_TRANSITION",
                    "title": "回售事项更新",
                    "summary": "正式上游事实发生变化。",
                },
            }
        )
    return {
        "snapshot_version": "daily-reminder-snapshot-v1",
        "status": "PASS",
        "snapshot_date": captured_at[:10],
        "captured_at": captured_at,
        "market_cutoff": captured_at[:10],
        "feed": {"items": items, "other_items": []},
    }


class ConvertibleWechatSnapshotTest(unittest.TestCase):
    def test_projection_uses_only_formal_reminder_snapshot(self) -> None:
        payload = build_convertible_wechat_snapshot(
            _source("2026-10-16T21:10:00+08:00", rows=2)
        )
        self.assertEqual(payload["source_owner"], "CONVERTIBLE_RUNTIME")
        self.assertEqual(payload["source_kind"], "WECHAT_UPSTREAM_SNAPSHOT")
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["row_count"], 2)
        self.assertEqual(payload["critical_empty_fields"], [])
        self.assertNotIn("source_url", payload)
        self.assertTrue(all(row["event_time"] for row in payload["rows"]))

    def test_zero_event_day_is_valid_ready_snapshot(self) -> None:
        payload = build_convertible_wechat_snapshot(
            _source("2026-10-16T21:10:00+08:00", rows=0)
        )
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["rows"], [])

    def test_archive_selection_is_cutoff_safe(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ConvertibleWechatSnapshotStore(tmp)
            early, _ = store.build_and_persist(
                _source("2026-10-16T21:10:00+08:00")
            )
            store.build_and_persist(
                _source("2026-10-16T23:10:00+08:00")
            )
            selected = store.load_latest_at_or_before(
                "2026-10-16T21:56:29+08:00"
            )
            self.assertIsNotNone(selected)
            self.assertEqual(selected["generated_at"], early["generated_at"])
            latest = Path(tmp) / "wechat_snapshot" / "convertible" / "latest.json"
            self.assertTrue(latest.exists())
            self.assertLess(latest.stat().st_size, 100_000)


if __name__ == "__main__":
    unittest.main()
