import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.ai_runtime.provider import ProviderResponse

from runtime.intelligence_radar.daily import (
    _combine_ai_usage,
    _merge_live_findings,
    run_jisilu_daily,
)
from runtime.intelligence_radar.increments import (
    mark_item_versions_analyzed,
    prepare_incremental_candidates,
    refresh_thread_capsules,
)
from runtime.intelligence_radar.storage import save_daily_result


def _candidate(text="今天新增执行细节"):
    return {
        "question_id": "100",
        "title": "长期套利讨论",
        "url": "https://example.com/100",
        "question_author": "甲",
        "context_segments": [
            {
                "segment_id": "question",
                "kind": "QUESTION",
                "locator_id": "100",
                "published_at": "2026-10-01 09:00",
                "text": "这是一个很长的原帖背景",
                "is_daily": False,
            }
        ],
        "daily_segments": [
            {
                "segment_id": "answer_10",
                "kind": "ANSWER",
                "locator_id": "10",
                "published_at": "2026-10-06 10:00",
                "text": text,
                "is_daily": True,
            }
        ],
    }


class IntelligenceRadarIncrementTest(unittest.TestCase):
    def test_exact_version_is_not_reanalyzed_but_edit_and_new_analysis_version_are(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.sqlite3"
            first = prepare_incremental_candidates(
                db,
                source="jisilu",
                candidates=[_candidate()],
                analysis_version="broad-v1",
            )
            self.assertEqual(first["new_version_count"], 1)
            self.assertEqual(first["ai_candidate_count"], 1)
            self.assertEqual(len(first["item_refs"]), 1)
            self.assertEqual(
                mark_item_versions_analyzed(
                    db,
                    item_refs=first["item_refs"],
                    analysis_version="broad-v1",
                ),
                1,
            )

            same = prepare_incremental_candidates(
                db,
                source="jisilu",
                candidates=[_candidate()],
                analysis_version="broad-v1",
            )
            self.assertEqual(same["new_version_count"], 0)
            self.assertEqual(same["skipped_version_count"], 1)
            self.assertEqual(same["ai_candidate_count"], 0)

            edited = prepare_incremental_candidates(
                db,
                source="jisilu",
                candidates=[_candidate("今天新增执行细节，且修改了结论")],
                analysis_version="broad-v1",
            )
            self.assertEqual(edited["new_version_count"], 1)
            self.assertEqual(edited["ai_candidate_count"], 1)

            new_analysis = prepare_incremental_candidates(
                db,
                source="jisilu",
                candidates=[_candidate()],
                analysis_version="broad-v2",
            )
            self.assertEqual(new_analysis["new_version_count"], 1)
            self.assertEqual(new_analysis["ai_candidate_count"], 1)

    def test_capsule_is_derived_from_final_nodes_without_extra_ai(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "radar.sqlite3"
            save_daily_result(
                db,
                run_date="2026-10-05",
                source="jisilu",
                scan_status="OK",
                scanned_at="2026-10-05T21:20:00+08:00",
                completed_at="2026-10-05T21:21:00+08:00",
                candidate_count=1,
                findings=[
                    {
                        "question_id": "100",
                        "object_name": "跨境ETF申购套利",
                        "node_title": "券商开闸时间影响排队",
                        "title": "长期套利讨论",
                        "url": "https://example.com/100",
                        "author": "甲",
                        "observed_at": "2026-10-05 19:00",
                        "finding_type": "EXECUTION_ISSUE",
                        "what_happened": "不同券商服务器接收委托时间不同",
                        "ai_understanding": "时点差会影响申购成功率",
                        "current_judgment": "值得继续实测",
                        "worth_follow_up": True,
                        "evidence_excerpt": "执行细节",
                    }
                ],
            )
            self.assertEqual(
                refresh_thread_capsules(
                    db,
                    source="jisilu",
                    thread_ids=["100"],
                ),
                1,
            )
            prepared = prepare_incremental_candidates(
                db,
                source="jisilu",
                candidates=[_candidate("第二天新增一条实测")],
                analysis_version="broad-v1",
            )
            row = prepared["candidates"][0]
            self.assertIn("券商开闸时间影响排队", row["context_capsule"])
            self.assertEqual(prepared["capsule_hit_count"], 1)

    def test_second_live_run_skips_same_increment_and_keeps_first_final(self):
        class FakeProvider:
            def __init__(self):
                self.calls = 0

            def complete(self, **kwargs):
                self.calls += 1
                is_final = "keep_ids" in kwargs["output_schema"].get("required", [])
                if is_final:
                    structured = {
                        "run_date": "2026-10-06",
                        "source": "jisilu",
                        "keep_ids": ["B001"],
                    }
                else:
                    structured = {
                        "run_date": "2026-10-06",
                        "source": "jisilu",
                        "batch_id": "2026-10-06:jisilu:001",
                        "findings": [
                            {
                                "question_id": "100",
                                "object_name": "跨境ETF申购套利",
                                "node_title": "新增执行细节",
                                "finding_type": "EXECUTION_ISSUE",
                                "what_happened": "今天新增执行细节",
                                "ai_understanding": "可能影响实际申购成功率",
                                "current_judgment": "值得继续跟踪",
                                "worth_follow_up": True,
                                "supporting_segment_ids": ["answer_10"],
                            }
                        ],
                    }
                return ProviderResponse(
                    provider="test",
                    model="test",
                    request_id=str(self.calls),
                    finish_reason="stop",
                    structured_output=structured,
                    tool_calls=[],
                    usage={
                        "prompt_tokens": 100,
                        "completion_tokens": 10,
                        "total_tokens": 110,
                        "prompt_cache_hit_tokens": 0,
                        "prompt_cache_miss_tokens": 100,
                    },
                    raw_response={},
                )

        scan = {
            "run_date": "2026-10-06",
            "source": "jisilu",
            "candidate_count": 1,
            "question_ref_count": 1,
            "candidates": [_candidate()],
            "feed_meta": {"question_ref_count": 1},
            "author_lane_meta": {"error_count": 0},
            "detail_error_count": 0,
            "detail_errors": [],
            "detail_without_daily_activity": 0,
            "coverage_warning_count": 0,
            "truncated_question_count": 0,
        }
        with tempfile.TemporaryDirectory() as tmp:
            provider = FakeProvider()
            with patch(
                "runtime.intelligence_radar.daily.collect_daily_candidates",
                return_value=scan,
            ):
                first = run_jisilu_daily(
                    data_root=Path(tmp),
                    run_date="2026-10-06",
                    provider=provider,
                    model="test",
                    run_mode="LIVE",
                )
                second = run_jisilu_daily(
                    data_root=Path(tmp),
                    run_date="2026-10-06",
                    provider=provider,
                    model="test",
                    run_mode="LIVE",
                )

            self.assertEqual(first["finding_count"], 1)
            self.assertEqual(first["ai_candidate_count"], 1)
            self.assertEqual(provider.calls, 2)
            self.assertEqual(second["ai_candidate_count"], 0)
            self.assertEqual(second["new_finding_count"], 0)
            self.assertEqual(second["finding_count"], 1)
            self.assertEqual(second["run_ai_usage"]["total_tokens"], 0)
            self.assertEqual(second["ai_usage"]["total_tokens"], 220)

    def test_live_merge_keeps_prior_nodes_and_daily_usage_accumulates(self):
        old = {
            "question_id": "1",
            "finding_type": "REAL_TEST",
            "node_title": "旧节点",
            "what_happened": "旧事实",
        }
        new = {
            "question_id": "2",
            "finding_type": "NEW_CASE",
            "node_title": "新节点",
            "what_happened": "新事实",
        }
        merged = _merge_live_findings([old], [new])
        self.assertEqual(len(merged), 2)

        usage = _combine_ai_usage(
            {
                "ai_metered": True,
                "ai_provider": "deepseek",
                "ai_model": "deepseek-chat",
                "ai_request_count": 2,
                "ai_prompt_tokens": 1000,
                "ai_completion_tokens": 100,
                "ai_total_tokens": 1100,
                "ai_cache_hit_tokens": 300,
                "ai_cache_miss_tokens": 700,
            },
            {
                "metered": True,
                "provider": "deepseek",
                "model": "deepseek-chat",
                "request_count": 1,
                "prompt_tokens": 400,
                "completion_tokens": 50,
                "total_tokens": 450,
                "cache_hit_tokens": 100,
                "cache_miss_tokens": 300,
            },
        )
        self.assertEqual(usage["request_count"], 3)
        self.assertEqual(usage["total_tokens"], 1550)
        self.assertEqual(usage["cache_hit_tokens"], 400)


if __name__ == "__main__":
    unittest.main()
