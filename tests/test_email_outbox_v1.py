from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.opportunity.email_outbox import build_pending_batch, load_outbox, mark_batch_sent


class EmailOutboxV1Test(unittest.TestCase):
    def _feed(self):
        return {
            "items": [{
                "notification_group_id": "NTF_1",
                "bond_code": "123456",
                "bond_name": "测试转债",
                "level": "IMMEDIATE",
                "created_at": "2026-10-01T01:02:03+00:00",
                "changes": [{"research_action": "FULL_V2_RESEARCH"}],
                "presentation": {
                    "title": "董事会提议下修",
                    "summary": "董事会已提出向下修正转股价格议案。",
                    "why": "这条新信息影响到下修。",
                    "risk": "仍需结合最终修正价和市场价格判断经济结果。",
                    "next_watch": "关注股东会结果与最终修正后的转股价。",
                },
            }]
        }

    def test_build_is_chinese_and_does_not_leak_machine_action(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.json"
            batch = build_pending_batch(
                feed=self._feed(),
                sent_notification_group_ids=set(),
                outbox_path=path,
                created_at="2026-10-01T01:05:00+00:00",
            )
            self.assertIsNotNone(batch)
            self.assertEqual(batch["status"], "PENDING")
            self.assertIn("董事会提议下修", batch["body"])
            self.assertNotIn("FULL_V2_RESEARCH", batch["body"])

    def test_same_group_set_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.json"
            a = build_pending_batch(feed=self._feed(), sent_notification_group_ids=set(), outbox_path=path)
            b = build_pending_batch(feed=self._feed(), sent_notification_group_ids=set(), outbox_path=path)
            self.assertEqual(a["batch_id"], b["batch_id"])
            self.assertEqual(len(load_outbox(path)["batches"]), 1)

    def test_sent_groups_create_no_new_batch(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.json"
            batch = build_pending_batch(
                feed=self._feed(),
                sent_notification_group_ids={"NTF_1"},
                outbox_path=path,
            )
            self.assertIsNone(batch)
            self.assertFalse(path.exists())

    def test_mark_sent_records_gmail_id(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "outbox.json"
            batch = build_pending_batch(feed=self._feed(), sent_notification_group_ids=set(), outbox_path=path)
            done = mark_batch_sent(
                outbox_path=path,
                batch_id=batch["batch_id"],
                gmail_message_id="gmail-123",
                sent_at="2026-10-01T01:06:00+00:00",
            )
            self.assertEqual(done["status"], "SENT")
            self.assertEqual(done["gmail_message_id"], "gmail-123")


if __name__ == "__main__":
    unittest.main()
