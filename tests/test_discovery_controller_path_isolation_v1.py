from __future__ import annotations

import unittest

from runtime.opportunity.discovery_controller import _aggregate_runtime_status


class DiscoveryControllerPathIsolationV1Test(unittest.TestCase):
    def test_partial_path_insufficient_data_is_structurally_usable(self) -> None:
        status, degraded = _aggregate_runtime_status({
            "MATURITY_CASH": {"status": "PASS"},
            "PUT": {"status": "PASS"},
            "DOWNWARD_REVISION": {"status": "INSUFFICIENT_DATA"},
        })
        self.assertEqual(status, "PASS")
        self.assertEqual(degraded, ["DOWNWARD_REVISION"])

    def test_real_runtime_failure_still_blocks(self) -> None:
        status, degraded = _aggregate_runtime_status({
            "MATURITY_CASH": {"status": "PASS"},
            "PUT": {"status": "FAIL"},
            "DOWNWARD_REVISION": {"status": "INSUFFICIENT_DATA"},
        })
        self.assertEqual(status, "INSUFFICIENT_DATA")
        self.assertEqual(degraded, ["DOWNWARD_REVISION"])


if __name__ == "__main__":
    unittest.main()
