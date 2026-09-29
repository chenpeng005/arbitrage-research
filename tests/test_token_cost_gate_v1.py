from __future__ import annotations

import unittest

from runtime.opportunity.incremental_event_router import route_event


class TokenCostGateV1Test(unittest.TestCase):
    def test_revision_expected_trigger_is_monitor_only(self) -> None:
        routed = route_event({
            "event_update_id": "EVU_expected",
            "event_family": "REVISION:EXPECTED_TRIGGER",
            "requires_semantic_audit": False,
        })
        self.assertEqual(routed["status"], "ROUTED")
        self.assertEqual(len(routed["routes"]), 1)
        route = routed["routes"][0]
        self.assertEqual(route["scope"], "DOWNWARD_REVISION")
        self.assertEqual(route["research_action"], "NONE")

    def test_rating_fact_update_skips_scope_ai(self) -> None:
        routed = route_event({
            "event_update_id": "EVU_rating",
            "event_family": "CREDIT:RATING_UPDATE",
            "materiality_status": "FACT_UPDATE",
            "requires_semantic_audit": False,
        })
        self.assertEqual(
            {x["scope"] for x in routed["routes"]},
            {"MATURITY_CASH", "PUT", "CREDIT_RISK"},
        )
        self.assertTrue(
            all(x["research_action"] == "NONE" for x in routed["routes"])
        )

    def test_rating_material_change_keeps_scope_audit(self) -> None:
        routed = route_event({
            "event_update_id": "EVU_rating_material",
            "event_family": "CREDIT:RATING_UPDATE",
            "materiality_status": "MATERIAL_CHANGE",
            "requires_semantic_audit": False,
        })
        self.assertTrue(
            all(
                x["research_action"] == "SEMANTIC_AUDIT"
                for x in routed["routes"]
            )
        )

    def test_support_fact_update_skips_scope_ai_but_material_does_not(self) -> None:
        routine = route_event({
            "event_update_id": "EVU_support",
            "event_family": "CREDIT:SUPPORT_OR_ASSET",
            "materiality_status": "FACT_UPDATE",
            "requires_semantic_audit": False,
        })
        self.assertEqual(
            {x["scope"] for x in routine["routes"]},
            {"MATURITY_CASH", "PUT"},
        )
        self.assertTrue(
            all(x["research_action"] == "NONE" for x in routine["routes"])
        )

        material = route_event({
            "event_update_id": "EVU_support_material",
            "event_family": "CREDIT:SUPPORT_OR_ASSET",
            "materiality_status": "MATERIAL_CHANGE",
            "requires_semantic_audit": False,
        })
        self.assertTrue(
            all(
                x["research_action"] == "SEMANTIC_AUDIT"
                for x in material["routes"]
            )
        )


if __name__ == "__main__":
    unittest.main()
