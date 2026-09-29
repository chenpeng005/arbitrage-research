from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.lof.qdii_proxy_registry import load_qdii_proxy_registry


class QdiiProxyRegistryTest(unittest.TestCase):
    def test_load_registry(self) -> None:
        payload = {
            "version": "X",
            "entries": [
                {
                    "fund_code": "161130",
                    "tracking_target": "纳斯达克100指数",
                    "proxy_type": "DIRECT_INDEX",
                    "proxy_symbol": "usNDX",
                    "history_symbol": "usNDX",
                    "currency": "USD",
                    "quality": "HIGH",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "r.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            rows = load_qdii_proxy_registry(p)

        self.assertEqual(rows["161130"].proxy_type, "DIRECT_INDEX")
        self.assertEqual(rows["161130"].quality, "HIGH")


if __name__ == "__main__":
    unittest.main()
