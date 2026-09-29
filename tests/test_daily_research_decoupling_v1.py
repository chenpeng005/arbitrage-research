from __future__ import annotations

import inspect
import unittest

from runtime.opportunity import full_runtime_controller
from runtime.opportunity.research_trigger import REVISION_TRIGGER_STATES


class DailyResearchDecouplingV1Test(unittest.TestCase):
    def test_near_revision_is_not_a_state_full_v2_trigger(self) -> None:
        self.assertNotIn("临近触发", REVISION_TRIGGER_STATES)
        self.assertEqual(
            REVISION_TRIGGER_STATES.get("满足条件"),
            "REVISION_EVENT_CONDITION_MET",
        )

    def test_light_mode_skips_task_and_evidence_pipeline(self) -> None:
        source = inspect.getsource(
            full_runtime_controller.run_opportunity_full_downstream
        )
        self.assertIn("if pending and not run_research:", source)
        self.assertIn("LIGHT_DAILY_UPDATE_RESEARCH_DEFERRED", source)
        self.assertIn("DEFERRED_TO_INDEPENDENT_RESEARCH_LANE", source)


if __name__ == "__main__":
    unittest.main()
