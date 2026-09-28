from __future__ import annotations

import unittest

from runtime.opportunity.incremental_notification_delivery import (
    REMINDER_PRESENTATION_VERSION,
    build_reminder_presentation,
)


class _OneRow:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _FakeConn:
    def execute(self, _sql, _params):
        return _OneRow(
            {
                "event_family_id": "123220:REVISION:BOARD_PROPOSAL",
                "occurred_at": "2026-09-29",
                "materiality_status": "MATERIAL_CHANGE",
                "payload_json": (
                    '{"fact_summary":"董事会已提出向下修正转股价格议案。",'
                    '"event_family":"REVISION:BOARD_PROPOSAL"}'
                ),
            }
        )


class ReminderV2Test(unittest.TestCase):
    def test_market_multi_path_card_keeps_change_and_economic_space(self) -> None:
        changes = [
            {
                "source_event_update_id": None,
                "scope_id": "PUT",
                "change_type": "ECONOMIC_ENTERED",
                "previous": {
                    "economic_status": "DROP",
                    "metrics": {"current_price_P": 100.048, "spread": -0.048},
                },
                "current": {
                    "economic_status": "KEEP",
                    "metrics": {"current_price_P": 98.244, "spread": 1.756},
                },
            },
            {
                "source_event_update_id": None,
                "scope_id": "DOWNWARD_REVISION",
                "change_type": "ECONOMIC_ENTERED",
                "previous": {
                    "economic_status": "DROP",
                    "metrics": {"current_price": 100.048, "current_cv": 103.2},
                },
                "current": {
                    "economic_status": "KEEP",
                    "metrics": {
                        "current_price": 98.244,
                        "current_cv": 95.51,
                        "discovery_spread": 33.4685,
                    },
                },
            },
        ]
        card = build_reminder_presentation(conn=None, changes=changes)
        self.assertEqual(card["version"], REMINDER_PRESENTATION_VERSION)
        self.assertEqual(card["title"], "回售与下修机会同时出现")
        self.assertEqual(card["direction"], "ENTERED")
        self.assertEqual(card["change_lines"][0]["previous_space_text"], "-0.05元")
        self.assertEqual(card["change_lines"][0]["current_space_text"], "+1.76元")
        self.assertEqual(card["change_lines"][1]["previous_space_text"], "无正空间")
        self.assertEqual(card["change_lines"][1]["current_space_text"], "+33.47元")

    def test_event_card_surfaces_human_fact_summary(self) -> None:
        changes = [
            {
                "source_event_update_id": "EVU_test",
                "scope_id": "DOWNWARD_REVISION",
                "change_type": "MATERIAL_EVENT",
                "research_action": "FULL_V2_RESEARCH",
            }
        ]
        card = build_reminder_presentation(conn=_FakeConn(), changes=changes)
        self.assertEqual(card["title"], "董事会提议下修")
        self.assertIn("董事会已提出", card["summary"])
        self.assertIn("下修", card["why"])
        self.assertIn("股东会", card["next_watch"])


if __name__ == "__main__":
    unittest.main()
