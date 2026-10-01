import unittest

from runtime.lof.commodity_proxy_registry import load_commodity_proxy_registry
from runtime.lof.context_builder import DEFAULT_COMMODITY_PROXY_REGISTRY


class R5QualityRegistryTest(unittest.TestCase):
    def test_gold_and_oil_quality_are_calibrated(self) -> None:
        registry = load_commodity_proxy_registry(DEFAULT_COMMODITY_PROXY_REGISTRY)

        for code in ("160719", "161116", "164701"):
            self.assertEqual(registry[code].proxy_quality, "MEDIUM")

        for code in ("160723", "161129", "161226"):
            self.assertEqual(registry[code].proxy_quality, "LOW")


if __name__ == "__main__":
    unittest.main()
