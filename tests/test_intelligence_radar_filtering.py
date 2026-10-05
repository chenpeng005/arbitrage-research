import json
import unittest

from runtime.intelligence_radar.filtering import (
    enrich_findings,
    filter_batch,
    normalize_finding_question_ids,
    validate_filter_output,
)
from runtime.ai_runtime.provider import ProviderResponse


def _input():
    return {
        "run_date": "2026-10-04",
        "source": "jisilu",
        "batch_id": "b1",
        "candidates": [
            {
                "question_id": "1",
                "title": "到账时间实测",
                "url": "https://www.jisilu.cn/question/1",
                "question_author": "甲",
                "activity_at": "2026-10-04 20:00",
                "context_segments": [
                    {
                        "segment_id": "question",
                        "kind": "QUESTION",
                        "is_daily": False,
                        "author": "甲",
                        "published_at": "2026-10-03 20:00",
                        "text": "讨论ETF申购",
                        "locator_id": "1",
                        "locator_url": "https://www.jisilu.cn/question/1",
                    }
                ],
                "daily_segments": [
                    {
                        "segment_id": "answer_10",
                        "kind": "ANSWER",
                        "is_daily": True,
                        "author": "乙",
                        "published_at": "2026-10-04 20:00",
                        "text": "今天实测到账慢了20分钟",
                        "locator_id": "10",
                        "locator_url": "https://www.jisilu.cn/question/1#answer_list_10",
                    }
                ],
            }
        ],
    }


def _finding(**overrides):
    row = {
        "question_id": "1",
        "object_name": "跨境ETF申购套利",
        "node_title": "到账时间比预期慢20分钟",
        "finding_type": "EXECUTION_ISSUE",
        "what_happened": "申购份额到账比预期慢",
        "ai_understanding": "到账时间会形成未对冲的价格暴露",
        "current_judgment": "执行风险，值得继续跟踪",
        "worth_follow_up": True,
        "supporting_segment_ids": ["answer_10"],
    }
    row.update(overrides)
    return row


class IntelligenceRadarFilteringTest(unittest.TestCase):
    def test_filter_validator_requires_daily_support(self) -> None:
        output = {
            "run_date": "2026-10-04",
            "source": "jisilu",
            "batch_id": "b1",
            "findings": [_finding()],
        }
        self.assertEqual(validate_filter_output(_input(), output), [])
        enriched = enrich_findings(_input(), output)
        self.assertEqual(enriched[0]["title"], "到账时间实测")
        self.assertEqual(enriched[0]["object_name"], "跨境ETF申购套利")
        self.assertEqual(enriched[0]["node_title"], "到账时间比预期慢20分钟")
        self.assertIn("20分钟", enriched[0]["evidence_excerpt"])
        self.assertEqual(enriched[0]["author"], "乙")
        self.assertEqual(
            enriched[0]["evidence"][0]["locator_url"],
            "https://www.jisilu.cn/question/1#answer_list_10",
        )
        self.assertTrue(enriched[0]["evidence"][0]["is_primary"])

    def test_normalize_rebinds_qid_to_unambiguous_segment_owner(self) -> None:
        payload = _input()
        payload["candidates"].append(
            {
                "question_id": "2",
                "title": "相似主题",
                "url": "https://www.jisilu.cn/question/2",
                "context_segments": [],
                "daily_segments": [
                    {
                        "segment_id": "answer_20",
                        "is_daily": True,
                        "author": "丙",
                        "text": "另一条回复",
                    }
                ],
            }
        )
        output = {
            "findings": [
                {
                    "question_id": "2",
                    "supporting_segment_ids": ["answer_10"],
                }
            ]
        }
        repairs = normalize_finding_question_ids(payload, output)
        self.assertEqual(output["findings"][0]["question_id"], "1")
        self.assertEqual(repairs[0]["from_question_id"], "2")
        self.assertEqual(repairs[0]["to_question_id"], "1")

    def test_filter_batch_retries_once_after_semantic_validation_error(self) -> None:
        class RetryProvider:
            def __init__(self):
                self.calls = 0

            def complete(self, **kwargs):
                self.calls += 1
                bad = {
                    "run_date": "2026-10-04",
                    "source": "jisilu",
                    "batch_id": "b1",
                    "findings": [_finding(supporting_segment_ids=["answer_other"])],
                }
                good = {
                    **bad,
                    "findings": [_finding(supporting_segment_ids=["answer_10"])],
                }
                return ProviderResponse(
                    provider="test",
                    model="test",
                    request_id=None,
                    finish_reason="stop",
                    structured_output=bad if self.calls == 1 else good,
                    tool_calls=[],
                    usage={},
                    raw_response={},
                )

        provider = RetryProvider()
        rows = filter_batch(
            run_date="2026-10-04",
            source="jisilu",
            batch_id="b1",
            candidates=_input()["candidates"],
            provider=provider,
            model="test",
        )
        self.assertEqual(provider.calls, 2)
        self.assertEqual(len(rows), 1)
        self.assertIn("20分钟", rows[0]["evidence_excerpt"])
        self.assertEqual(rows[0]["object_name"], "跨境ETF申购套利")

    def test_author_lane_metadata_does_not_bias_ai_input(self) -> None:
        class CaptureProvider:
            def __init__(self):
                self.payload = None

            def complete(self, **kwargs):
                self.payload = json.loads(kwargs["messages"][0]["content"])
                return ProviderResponse(
                    provider="test",
                    model="test",
                    request_id=None,
                    finish_reason="stop",
                    structured_output={
                        "run_date": "2026-10-04",
                        "source": "jisilu",
                        "batch_id": "b1",
                        "findings": [_finding()],
                    },
                    tool_calls=[],
                    usage={},
                    raw_response={},
                )

        candidates = _input()["candidates"]
        candidates[0]["discovery_paths"] = ["AUTHOR_LANE"]
        candidates[0]["author_lane_authors"] = ["gaigai777"]
        provider = CaptureProvider()
        rows = filter_batch(
            run_date="2026-10-04",
            source="jisilu",
            batch_id="b1",
            candidates=candidates,
            provider=provider,
            model="test",
        )
        ai_candidate = provider.payload["candidates"][0]
        self.assertNotIn("discovery_paths", ai_candidate)
        self.assertNotIn("author_lane_authors", ai_candidate)
        self.assertEqual(rows[0]["discovery_paths"], ["AUTHOR_LANE"])
        self.assertEqual(rows[0]["author_lane_authors"], ["gaigai777"])

    def test_filter_validator_rejects_context_only_support(self) -> None:
        output = {
            "run_date": "2026-10-04",
            "source": "jisilu",
            "batch_id": "b1",
            "findings": [
                _finding(
                    finding_type="NEW_MECHANISM",
                    what_happened="只是复述旧帖内容",
                    ai_understanding="没有当天新增证据",
                    current_judgment="不应作为今日发现",
                    worth_follow_up=False,
                    supporting_segment_ids=["question"],
                )
            ],
        }
        errors = validate_filter_output(_input(), output)
        self.assertTrue(any("daily segment" in item for item in errors))

    def test_filter_validator_requires_object_and_node_title(self) -> None:
        output = {
            "run_date": "2026-10-04",
            "source": "jisilu",
            "batch_id": "b1",
            "findings": [_finding(object_name="", node_title="")],
        }
        errors = validate_filter_output(_input(), output)
        self.assertTrue(any("object_name" in item for item in errors))
        self.assertTrue(any("node_title" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
