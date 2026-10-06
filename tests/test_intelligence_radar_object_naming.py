import unittest

from runtime.intelligence_radar.filtering import validate_filter_output


def _input():
    return {
        "run_date": "2026-10-06",
        "source": "jisilu",
        "batch_id": "naming",
        "candidates": [
            {
                "question_id": "1",
                "title": "岭南转债讨论",
                "url": "https://example.com/1",
                "context_segments": [],
                "daily_segments": [
                    {
                        "segment_id": "a1",
                        "is_daily": True,
                        "text": "正股退市后讨论转债兑付安排",
                    }
                ],
            }
        ],
    }


def _output(object_name: str):
    return {
        "run_date": "2026-10-06",
        "source": "jisilu",
        "batch_id": "naming",
        "findings": [
            {
                "question_id": "1",
                "object_name": object_name,
                "node_title": "退市后转债兑付安排出现新讨论",
                "finding_type": "NEW_CASE",
                "what_happened": "讨论正股退市后的转债兑付安排",
                "ai_understanding": "兑付路径会改变持有人的实际回收结果",
                "current_judgment": "值得继续跟踪",
                "worth_follow_up": True,
                "supporting_segment_ids": ["a1"],
            }
        ],
    }


class IntelligenceRadarObjectNamingTest(unittest.TestCase):
    def test_rejects_bare_code(self):
        errors = validate_filter_output(_input(), _output("002667"))
        self.assertTrue(any("bare security/code" in x for x in errors))

    def test_rejects_bare_convertible_name(self):
        errors = validate_filter_output(_input(), _output("岭南转债"))
        self.assertTrue(any("bare security/code" in x for x in errors))

    def test_rejects_bare_ticker_in_name(self):
        errors = validate_filter_output(_input(), _output("宁德时代（300750.SZ）"))
        self.assertTrue(any("bare security/code" in x for x in errors))

    def test_accepts_attention_theme(self):
        errors = validate_filter_output(_input(), _output("岭南转债退市兑付风险"))
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
