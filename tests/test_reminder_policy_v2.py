from __future__ import annotations

import unittest

from runtime.opportunity.incremental_change_notification import (
    attach_notification_decision,
    deep_research_change,
    market_change,
)


class ReminderPolicyV2Test(unittest.TestCase):
    def test_economic_entry_is_silent(self) -> None:
        change = market_change(
            bond_code="111025",
            bond_name="圣泉转债",
            path_id="DOWNWARD_REVISION",
            snapshot_id="snap-1",
            previous_economic_status="DROP",
            current_economic_status="KEEP",
            previous_metrics={"discovery_spread": -4.25},
            current_metrics={"discovery_spread": 2.59},
            research_action="REUSE_GATE",
        )
        self.assertEqual(
            attach_notification_decision(change)["notification_level"],
            "SILENT",
        )

    def test_unresearched_exit_is_silent(self) -> None:
        change = market_change(
            bond_code="113054",
            bond_name="绿动转债",
            path_id="DOWNWARD_REVISION",
            snapshot_id="snap-2",
            previous_economic_status="KEEP",
            current_economic_status="DROP",
            previous_metrics={"discovery_spread": 0.65},
            current_metrics={"discovery_spread": -0.19},
            research_action="NONE",
            previous_research_status=None,
            previous_research_attention=False,
        )
        self.assertEqual(
            attach_notification_decision(change)["notification_level"],
            "SILENT",
        )

    def test_researched_exit_is_immediate(self) -> None:
        change = market_change(
            bond_code="123151",
            bond_name="康医转债",
            path_id="MATURITY_CASH",
            snapshot_id="snap-3",
            previous_economic_status="KEEP",
            current_economic_status="DROP",
            previous_metrics={"spread_C_minus_P": 0.36},
            current_metrics={"spread_C_minus_P": -0.47},
            research_action="NONE",
            previous_research_status="COMPLETED",
            previous_research_attention=True,
        )
        decision = attach_notification_decision(change)
        self.assertEqual(decision["notification_level"], "IMMEDIATE")
        self.assertTrue(decision["notification_required"])

    def test_new_full_v2_research_is_immediate(self) -> None:
        change = deep_research_change(
            bond_code="123220",
            bond_name="易瑞转债",
            path_id="DOWNWARD_REVISION",
            event_update_id="EVU_test",
            event_family="REVISION:BOARD_PROPOSAL",
            reason="REVISION_GOVERNANCE_NODE_CHANGED",
        )
        decision = attach_notification_decision(change)
        self.assertEqual(decision["notification_level"], "IMMEDIATE")
        self.assertTrue(decision["notification_required"])


if __name__ == "__main__":
    unittest.main()
