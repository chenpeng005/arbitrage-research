from __future__ import annotations
import unittest
from runtime.lof.csi_component_proxy import is_csi_component_proxy_candidate

class LofCsiHSeriesTest(unittest.TestCase):
    def test_h_series_is_component_proxy_candidate(self) -> None:
        self.assertTrue(is_csi_component_proxy_candidate("H30094"))
        self.assertFalse(is_csi_component_proxy_candidate("CBA00101"))

if __name__ == "__main__":
    unittest.main()