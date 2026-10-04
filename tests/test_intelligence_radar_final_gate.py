import unittest

from runtime.intelligence_radar.final_gate import (
    _gate_input,
    apply_final_gate_selection,
    validate_final_gate_output,
)


class IntelligenceRadarFinalGateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.broad = [
            {
                "question_id": "1",
                "title": "执行差异",
                "finding_type": "EXECUTION_ISSUE",
                "what_happened": "份额到账晚20分钟",
                "ai_understanding": "影响套利暴露",
                "current_judgment": "值得跟踪",
                "worth_follow_up": True,
            },
            {
                "question_id": "2",
                "title": "普通观点",
                "finding_type": "OTHER",
                "what_happened": "讨论市场走势",
                "ai_understanding": "一般投资观点",
                "current_judgment": "不重要",
                "worth_follow_up": False,
            },
        ]
        self.payload = _gate_input("2026-10-04", "jisilu", self.broad)

    def test_valid_selection(self) -> None:
        output = {
            "run_date": "2026-10-04",
            "source": "jisilu",
            "keep_ids": ["B001"],
        }
        self.assertEqual(
            validate_final_gate_output(self.payload, output),
            [],
        )
        selected = apply_final_gate_selection(
            self.payload,
            self.broad,
            output,
        )
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["question_id"], "1")

    def test_unknown_id_is_rejected(self) -> None:
        output = {
            "run_date": "2026-10-04",
            "source": "jisilu",
            "keep_ids": ["B999"],
        }
        errors = validate_final_gate_output(self.payload, output)
        self.assertTrue(any("unknown" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
