import unittest

from runtime.intelligence_radar.xueqiu import (
    build_increment_candidates,
    classify_status,
    collect_author_shadow,
    collect_hot_exploration,
    collect_xueqiu_shadow,
    hydrate_status_if_needed,
    normalize_comment,
    normalize_status,
)


class FakeClient:
    def __init__(self):
        class Stats:
            requests = 2
            bootstrap_requests = 0
            timeline_requests = 1
            comment_requests = 1
            detail_requests = 0
            hot_requests = 1
        self.stats = Stats()

    def user_timeline(self, user_id, *, page=1, count=20):
        if page > 1:
            return {"statuses": []}
        return {
            "statuses": [
                {
                    "id": 100,
                    "user_id": int(user_id),
                    "created_at": 1791230400000,
                    "edited_at": 1791230460000,
                    "commentId": 0,
                    "retweet_status_id": 0,
                    "description": "<b>原创</b> 内容",
                    "target": f"/{user_id}/100",
                    "reply_count": 2,
                    "user": {"screen_name": "作者甲"},
                },
                {
                    "id": 101,
                    "user_id": int(user_id),
                    "created_at": 1791234000000,
                    "commentId": 555,
                    "retweet_status_id": 999,
                    "description": "回复<a>@乙</a>: 新的执行信息",
                    "target": f"/{user_id}/101",
                    "user": {"screen_name": "作者甲"},
                    "retweeted_status": {
                        "id": 999,
                        "user_id": 2,
                        "created_at": 1791144000000,
                        "description": "父帖背景",
                        "target": "/2/999",
                        "user": {"screen_name": "乙"},
                    },
                },
            ],
            "maxPage": 1,
        }

    def status_detail(self, status_id):
        self.stats.detail_requests += 1
        return {
            "id": int(status_id), "user_id": 1, "created_at": 1791230400000,
            "edited_at": 1791230460000, "commentId": 0, "retweet_status_id": 0,
            "description": "完整长文正文", "target": f"/1/{status_id}",
            "truncated": False, "user": {"screen_name": "作者甲"}
        }

    def hot_list(self, *, max_id=-1, size=10):
        return {
            "items": [
                {"original_status": {
                    "id": 200, "user_id": 8, "created_at": 1791230400000,
                    "commentId": 0, "retweet_status_id": 0,
                    "description": "热榜探索", "target": "/8/200",
                    "user": {"screen_name": "探索作者"}
                }}
            ],
            "next_max_id": -1,
        }

    def status_comments(self, status_id, *, page=1, count=20):
        return {
            "comments": [
                {
                    "id": 777,
                    "statusId": int(status_id),
                    "user_id": 3,
                    "created_at": 1791237600000,
                    "description": "评论里的<b>反例</b>",
                    "in_reply_to_comment_id": 0,
                    "root_in_reply_to_status_id": int(status_id),
                    "user": {"screen_name": "丙"},
                }
            ],
            "maxPage": 1,
        }


class IntelligenceRadarXueqiuTest(unittest.TestCase):
    def test_status_classification_uses_reply_identity_fields(self):
        self.assertEqual(classify_status({"commentId": 0, "retweet_status_id": 0}), "POST")
        self.assertEqual(classify_status({"commentId": 12, "retweet_status_id": 34}), "REPLY")
        self.assertEqual(classify_status({"commentId": 0, "retweet_status_id": 34}), "REPOST")

    def test_reply_prefers_comment_id_and_keeps_parent_context(self):
        row = normalize_status(
            {
                "id": 101,
                "user_id": 1,
                "created_at": 1791234000000,
                "commentId": 555,
                "retweet_status_id": 999,
                "description": "回复<a>@乙</a>: 新信息",
                "target": "/1/101",
                "user": {"screen_name": "甲"},
                "retweeted_status": {
                    "id": 999,
                    "user_id": 2,
                    "created_at": 1791144000000,
                    "description": "父帖背景",
                    "user": {"screen_name": "乙"},
                },
            }
        )
        self.assertEqual(row["item_type"], "REPLY")
        self.assertEqual(row["item_id"], "555")
        self.assertEqual(row["status_id"], "101")
        self.assertEqual(row["parent_id"], "999")
        self.assertIn("父帖背景", row["parent_context"]["content"])

    def test_comment_has_stable_comment_id_and_thread_parent(self):
        row = normalize_comment(
            {
                "id": 777,
                "statusId": 100,
                "user_id": 3,
                "created_at": 1791237600000,
                "description": "反例",
                "root_in_reply_to_status_id": 100,
            },
            fallback_status_id="100",
        )
        self.assertEqual(row["item_type"], "COMMENT")
        self.assertEqual(row["item_id"], "777")
        self.assertEqual(row["parent_id"], "100")

    def test_items_map_to_existing_increment_ledger_candidate_shape(self):
        items = [
            {
                "source": "xueqiu", "item_type": "REPLY", "item_id": "555",
                "status_id": "101", "parent_id": "999", "author_name": "甲",
                "published_at": "2026-10-06T10:00:00+08:00", "content": "新增实测",
                "locator_url": "https://www.xueqiu.com/1/101",
                "parent_context": {
                    "status_id": "999", "author_name": "乙",
                    "published_at": "2026-10-05T09:00:00+08:00",
                    "content": "父帖背景", "target": "/2/999"
                }
            }
        ]
        candidates = build_increment_candidates(items)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["question_id"], "999")
        self.assertEqual(candidates[0]["daily_segments"][0]["locator_id"], "555")
        self.assertEqual(candidates[0]["daily_segments"][0]["kind"], "XQ_REPLY")
        self.assertFalse(candidates[0]["context_segments"][0]["is_daily"])

    def test_shadow_collector_filters_target_date_and_is_ai_free(self):
        result = collect_author_shadow(
            run_date="2026-10-06",
            authors=[{"user_id": "1", "label": "作者甲"}],
            client=FakeClient(),
            max_pages=1,
            include_comments=True,
            max_comment_threads=1,
        )
        self.assertEqual(result["mode"], "SHADOW_COLLECT_ONLY")
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["item_type_counts"], {"POST": 1, "REPLY": 1, "COMMENT": 1})
        self.assertEqual(result["request_stats"]["timeline_requests"], 1)
        self.assertEqual(result["request_stats"]["comment_requests"], 1)

    def test_truncated_status_is_hydrated_before_hashing_or_ai_shape(self):
        client = FakeClient()
        row, error = hydrate_status_if_needed(client, {"id": 123, "truncated": True})
        self.assertIsNone(error)
        self.assertEqual(row["description"], "完整长文正文")
        self.assertEqual(client.stats.detail_requests, 1)

    def test_hot_exploration_is_separate_non_keyword_discovery_path(self):
        result = collect_hot_exploration(
            run_date="2026-10-06", client=FakeClient(), max_pages=1
        )
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(
            result["items"][0]["discovery_paths"], ["XUEQIU_HOT_EXPLORATION"]
        )
        self.assertIn("not full-site", result["coverage_note"])

    def test_combined_shadow_keeps_author_and_hot_lanes_separate(self):
        result = collect_xueqiu_shadow(
            run_date="2026-10-06",
            authors=[{"user_id": "1", "label": "作者甲"}],
            client=FakeClient(),
            author_max_pages=1,
            include_hot=True,
            hot_max_pages=1,
        )
        self.assertEqual(result["error_count"], 0)
        self.assertEqual(result["item_count"], 3)
        self.assertEqual(result["discovery_path_counts"]["XUEQIU_AUTHOR_SHADOW"], 2)
        self.assertEqual(result["discovery_path_counts"]["XUEQIU_HOT_EXPLORATION"], 1)
        self.assertGreaterEqual(result["candidate_count"], 2)


if __name__ == "__main__":
    unittest.main()
