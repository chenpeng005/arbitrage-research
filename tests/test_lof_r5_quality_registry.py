import unittest

from runtime.lof.commodity_proxy_registry import load_commodity_proxy_registry
from runtime.lof.context_builder import DEFAULT_COMMODITY_PROXY_REGISTRY


class R5QualityRegistryTest(unittest.TestCase):
    def test_gold_and_oil_quality_are_calibrated(self) -> None:
        registry = load_commodity_proxy_registry(DEFAULT_COMMODITY_PROXY_REGISTRY)

        for code in ("160719", "161116", "164701"):
            self.assertEqual(registry[code].proxy_quality, "MEDIUM")

        for code in ("160723", "161129", "161226", "501018"):
            self.assertEqual(registry[code].proxy_quality, "LOW")


    def test_501018_uses_audited_wti_brent_basket(self) -> None:
        registry = load_commodity_proxy_registry(DEFAULT_COMMODITY_PROXY_REGISTRY)
        row = registry["501018"]
        self.assertEqual(row.status, "RESOLVED")
        self.assertEqual(row.currency, "USD")
        self.assertEqual(str(row.exposure_ratio), "1.0")
        self.assertEqual(len(row.components), 2)
        self.assertEqual(
            [(x.history_symbol, x.live_market, x.live_code, str(x.weight)) for x in row.components],
            [
                ("CL", "102", "CL00Y", "0.60"),
                ("OIL", "112", "B00Y", "0.40"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
