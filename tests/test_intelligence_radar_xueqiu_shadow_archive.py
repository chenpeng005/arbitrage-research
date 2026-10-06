import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime.intelligence_radar.xueqiu_shadow_archive import (
    _logical_dates,
    merge_collection_into_archive,
)


class XueqiuShadowArchiveTest(unittest.TestCase):
    def test_morning_run_includes_previous_date(self):
        now = datetime(2026, 10, 6, 7, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(_logical_dates(now), ["2026-10-05", "2026-10-06"])

    def test_non_morning_run_only_uses_today(self):
        now = datetime(2026, 10, 6, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertEqual(_logical_dates(now), ["2026-10-06"])

    def test_archive_dedupes_seen_item_and_tracks_edit(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "2026-10-06.json"
            first = {
                "error_count": 0,
                "errors": [],
                "items": [{
                    "item_type": "POST",
                    "item_id": "100",
                    "status_id": "100",
                    "author_id": "1",
                    "author_name": "甲",
                    "published_at": "2026-10-06T08:00:00+08:00",
                    "edited_at": None,
                    "content": "第一次正文",
                    "locator_url": "https://www.xueqiu.com/1/100",
                    "discovery_paths": ["XUEQIU_HOT_EXPLORATION"],
                }],
                "author_lane": {"item_count": 0},
                "hot_exploration": {"pages": 2},
            }
            summary1 = merge_collection_into_archive(
                path=path,
                logical_date="2026-10-06",
                collected_at="2026-10-06T08:30:00+08:00",
                result=first,
                request_stats={"requests": 3},
            )
            self.assertEqual(summary1["new_item_count"], 1)
            self.assertEqual(summary1["archive_item_count"], 1)

            summary2 = merge_collection_into_archive(
                path=path,
                logical_date="2026-10-06",
                collected_at="2026-10-06T12:30:00+08:00",
                result=first,
                request_stats={"requests": 2},
            )
            self.assertEqual(summary2["new_item_count"], 0)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["items"][0]["seen_count"], 2)
            self.assertEqual(payload["items"][0]["edit_count"], 0)

            edited = json.loads(json.dumps(first))
            edited["items"][0]["content"] = "第二次正文"
            edited["items"][0]["edited_at"] = "2026-10-06T16:00:00+08:00"
            summary3 = merge_collection_into_archive(
                path=path,
                logical_date="2026-10-06",
                collected_at="2026-10-06T17:00:00+08:00",
                result=edited,
                request_stats={"requests": 2},
            )
            self.assertEqual(summary3["changed_item_count"], 1)
            payload = json.loads(path.read_text(encoding="utf-8"))
            item = payload["items"][0]
            self.assertEqual(item["seen_count"], 3)
            self.assertEqual(item["edit_count"], 1)
            self.assertEqual(len(item["prior_content_hashes"]), 1)
            self.assertEqual(item["excerpt"], "第二次正文")

    def test_archive_stores_excerpt_not_full_body(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "2026-10-06.json"
            body = "甲" * 1000
            result = {
                "error_count": 0,
                "errors": [],
                "items": [{
                    "item_type": "POST", "item_id": "100", "status_id": "100",
                    "author_id": "1", "author_name": "甲",
                    "published_at": "2026-10-06T08:00:00+08:00",
                    "content": body, "discovery_paths": ["XUEQIU_HOT_EXPLORATION"],
                }],
                "author_lane": {"item_count": 0}, "hot_exploration": {"pages": 1},
            }
            merge_collection_into_archive(
                path=path, logical_date="2026-10-06",
                collected_at="2026-10-06T08:30:00+08:00", result=result,
                excerpt_chars=120,
            )
            payload = json.loads(path.read_text(encoding="utf-8"))
            item = payload["items"][0]
            self.assertEqual(len(item["excerpt"]), 120)
            self.assertNotIn(body, path.read_text(encoding="utf-8"))
            self.assertEqual(len(item["content_sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
