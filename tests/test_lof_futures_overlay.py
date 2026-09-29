from __future__ import annotations

import unittest
from decimal import Decimal

from runtime.lof.futures_overlay import parse_eastmoney_global_futures


class LofFuturesOverlayTest(unittest.TestCase):
    def test_parse_nq_overlay(self) -> None:
        payload = {
            "qt": {
                "dm": "NQ00Y",
                "p": 30426.31,
                "fzjsj": 30566.25,
                "zdf": -0.46,
                "jysj": 130725,
                "tjsrq": 20260929,
            }
        }
        row = parse_eastmoney_global_futures(
            payload,
            code="NQ00Y",
        )
        self.assertIsNone(row.error)
        self.assertEqual(row.current, Decimal("30426.31"))
        self.assertEqual(row.previous_settlement, Decimal("30566.25"))
        self.assertLess(row.adjustment_return, Decimal("0"))

    def test_missing_settlement_is_explicit(self) -> None:
        row = parse_eastmoney_global_futures(
            {"qt": {"dm": "NQ00Y", "p": 30426.31}},
            code="NQ00Y",
        )
        self.assertEqual(row.error, "INVALID_OR_MISSING_FUTURES_QUOTE")
        self.assertIsNone(row.adjustment_return)


if __name__ == "__main__":
    unittest.main()
