from decimal import Decimal
from pathlib import Path
import unittest

from runtime.lof.qdii_proxy_registry import load_qdii_proxy_registry


class QdiiProxyResolutionV1Test(unittest.TestCase):
    def registry(self):
        path = (
            Path(__file__).resolve().parents[1]
            / "runtime"
            / "lof"
            / "data"
            / "qdii_index_proxy_registry_v0_1.json"
        )
        return load_qdii_proxy_registry(path)

    def test_162415_uses_xly_same_index_proxy(self):
        row = self.registry()["162415"]
        self.assertEqual(row.proxy_type, "ETF_SAME_INDEX")
        self.assertEqual(row.proxy_symbol, "usXLY")
        self.assertEqual(row.history_symbol, "XLY.AM")
        self.assertEqual(row.currency, "USD")
        self.assertEqual(row.quality, "MEDIUM")
        self.assertEqual(row.exposure_ratio, Decimal("0.95"))

    def test_161124_remains_unresolved_without_live_hssi_source(self):
        row = self.registry()["161124"]
        self.assertEqual(row.proxy_type, "UNRESOLVED")
        self.assertIsNone(row.proxy_symbol)
        self.assertEqual(
            row.unresolved_reason,
            "NO_LIVE_HSSI_SOURCE_IN_CURRENT_RUNTIME",
        )


if __name__ == "__main__":
    unittest.main()
