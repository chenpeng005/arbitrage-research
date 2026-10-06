import json
import tempfile
import unittest
from pathlib import Path

from runtime.intelligence_radar.guba import (
    _json_after_marker,
    _parse_jsonp,
    collect_guba_shadow,
    flatten_replies,
    normalize_post,
)
from runtime.intelligence_radar.guba_shadow_archive import merge_collection_into_archive


class FakeClient:
    def __init__(self):
        class Stats:
            requests = 5
            index_requests = 1
            bar_requests = 1
            detail_requests = 2
            reply_requests = 1
        self.stats = Stats()

    def bar_page(self, code, *, page=1):
        return {"re": [
            {"post_id": 100, "post_title": "老帖", "post_publish_time": "2026-10-05 10:00:00", "post_last_time": "2026-10-06 11:00:00", "post_comment_count": 2},
            {"post_id": 101, "post_title": "新帖", "post_publish_time": "2026-10-06 09:00:00", "post_last_time": "2026-10-06 09:00:00", "post_comment_count": 0},
        ]}

    def post_detail(self, code, post_id):
        return {
            "post_id": int(post_id), "post_title": "新帖", "post_content": "<p>现金选择权实操</p>",
            "post_publish_time": "2026-10-06 09:00:00", "post_last_time": "2026-10-06 09:00:00",
            "post_comment_count": 0, "post_like_count": 0,
            "post_user": {"user_id": "1", "user_nickname": "甲"},
            "post_guba": {"stockbar_code": code, "stockbar_name": "测试股"},
        }

    def post_replies(self, post_id, *, page=1, page_size=50):
        return {"re": [{
            "reply_id": 501, "source_post_id": int(post_id), "user_id": "2",
            "reply_publish_time": "2026-10-06 11:00:00", "reply_text": "华泰手机端不支持，电脑端可以",
            "reply_user": {"user_nickname": "乙"}, "reply_like_count": 1,
            "child_replys": [{
                "reply_id": 502, "source_post_id": int(post_id), "user_id": "3",
                "reply_publish_time": "2026-10-06 11:05:00", "reply_text": "我这里显示废单",
                "reply_user": {"user_nickname": "丙"}, "source_reply": [{"source_reply_id": 501}],
            }]
        }]}

    def index_html(self):
        return '<ul class="newlist"><li><a class="note" href="/news,000001,200.html">全站帖</a><cite class="date">10-06 12:00</cite></li></ul>'


def test_detail(post_id=200):
    return {
        "post_id": int(post_id), "post_title": "全站帖", "post_content": "规则变化线索",
        "post_publish_time": "2026-10-06 12:00:00", "post_last_time": "2026-10-06 12:00:00",
        "post_comment_count": 0, "post_user": {"user_id": "4", "user_nickname": "丁"},
        "post_guba": {"stockbar_code": "000001", "stockbar_name": "测试2"},
    }


FakeClient._detail_original = FakeClient.post_detail
def _detail(self, code, post_id):
    if str(post_id) == "200":
        return test_detail(post_id)
    return self._detail_original(code, post_id)
FakeClient.post_detail = _detail


class GubaTest(unittest.TestCase):
    def test_js_object_and_jsonp_parsers(self):
        self.assertEqual(_json_after_marker('x var article_list={"re":[1]}; y', 'var article_list=')["re"], [1])
        self.assertEqual(_parse_jsonp('cb({"re":[2]})')["re"], [2])

    def test_post_normalization_uses_stable_post_id(self):
        row = normalize_post(test_detail(), discovery_path="GUBA_GLOBAL_EXPLORATION")
        self.assertEqual(row["item_type"], "POST")
        self.assertEqual(row["item_id"], "200")
        self.assertEqual(row["bar_code"], "000001")
        self.assertEqual(row["content"], "规则变化线索")

    def test_reply_flattening_preserves_child_identity(self):
        payload = FakeClient().post_replies("100")
        rows = flatten_replies(payload, code="000016", post_title="老帖", discovery_path="GUBA_OBJECT_FOLLOWUP")
        self.assertEqual([row["item_id"] for row in rows], ["501", "502"])
        self.assertEqual(rows[1]["in_reply_to_reply_id"], "501")

    def test_shadow_combines_object_and_global_without_ai(self):
        result = collect_guba_shadow(
            run_date="2026-10-06",
            bars=[{"code": "000016", "name": "测试股", "reason": "test"}],
            client=FakeClient(),
            include_global=True,
            global_max_posts=3,
        )
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["item_type_counts"], {"REPLY": 2, "POST": 2})
        self.assertEqual(result["discovery_path_counts"]["GUBA_OBJECT_FOLLOWUP"], 3)
        self.assertEqual(result["discovery_path_counts"]["GUBA_GLOBAL_EXPLORATION"], 1)

    def test_archive_second_run_adds_seen_not_duplicate(self):
        result = collect_guba_shadow(
            run_date="2026-10-06",
            bars=[{"code": "000016", "name": "测试股", "reason": "test"}],
            client=FakeClient(), include_global=False,
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "2026-10-06.json"
            first = merge_collection_into_archive(path=path, logical_date="2026-10-06", collected_at="2026-10-06T12:00:00+08:00", result=result)
            second = merge_collection_into_archive(path=path, logical_date="2026-10-06", collected_at="2026-10-06T12:30:00+08:00", result=result)
            self.assertEqual(first["new_item_count"], 3)
            self.assertEqual(second["new_item_count"], 0)
            payload = json.loads(path.read_text())
            self.assertTrue(all(row["seen_count"] == 2 for row in payload["items"]))


if __name__ == "__main__":
    unittest.main()
