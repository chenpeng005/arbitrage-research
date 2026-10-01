from decimal import Decimal
from pathlib import Path
import unittest

from runtime.lof.commodity_proxy_registry import load_commodity_proxy_registry


class CommodityProxyRegistryTest(unittest.TestCase):
    def test_164701_gold_fof_is_resolved_to_gc_fx_bridge(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "runtime" / "lof" / "data"
            / "commodity_proxy_registry_v0_1.json"
        )
        entry = load_commodity_proxy_registry(path)["164701"]

        self.assertEqual(entry.status, "RESOLVED")
        self.assertEqual(entry.commodity_history_symbol, "GC")
        self.assertEqual(entry.commodity_live_market, "101")
        self.assertEqual(entry.commodity_live_code, "GC00Y")
        self.assertEqual(entry.currency, "USD")
        self.assertEqual(entry.exposure_ratio, Decimal("1.0"))
        self.assertEqual(entry.proxy_quality, "MEDIUM")


if __name__ == "__main__":
    unittest.main()
