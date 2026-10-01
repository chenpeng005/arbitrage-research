from decimal import Decimal
from pathlib import Path
import unittest

from runtime.lof.commodity_proxy_registry import load_commodity_proxy_registry


class R5Gold164701RegistryTest(unittest.TestCase):
    def test_164701_uses_gc_usdcny_medium_proxy(self) -> None:
        path = (
            Path(__file__).resolve().parents[1]
            / "runtime"
            / "lof"
            / "data"
            / "commodity_proxy_registry_v0_1.json"
        )
        row = load_commodity_proxy_registry(path)["164701"]
        self.assertEqual(row.status, "RESOLVED")
        self.assertEqual(row.commodity_history_symbol, "GC")
        self.assertEqual(row.commodity_live_market, "101")
        self.assertEqual(row.commodity_live_code, "GC00Y")
        self.assertEqual(row.currency, "USD")
        self.assertEqual(row.exposure_ratio, Decimal("1.0"))
        self.assertEqual(row.proxy_quality, "MEDIUM")
        self.assertEqual(row.anchor_mode, "HISTORY_CLOSE")


if __name__ == "__main__":
    unittest.main()
