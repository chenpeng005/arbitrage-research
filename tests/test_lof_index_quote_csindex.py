from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.index_quote_csindex import (
    parse_csindex_intraday_payload,
    supports_csindex_official_quote,
)


TZ = ZoneInfo("Asia/Shanghai")


class LofCsindexQuoteTest(unittest.TestCase):
    def test_parse_intraday_header(self) -> None:
        payload = {
            "code": "200",
            "msg": "Success",
            "data": {
                "intraDayHeader": {
                    "indexCode": "930875",
                    "tradeDate": "2026-09-30",
                    "tradeTime": "11:29:57",
                    "openToday": 1966.29,
                    "closePre": 1953.07,
                    "current": 1964.22,
                    "change": 11.16,
                    "changePct": 0.57,
                },
                "intraDayPerfList": [],
            },
        }
        row = parse_csindex_intraday_payload(
            payload,
            index_code="930875",
        )
        self.assertEqual(row.code, "930875")
        self.assertEqual(row.current, Decimal("1964.22"))
        self.assertEqual(row.previous_close, Decimal("1953.07"))
        self.assertEqual(row.source, "CSI_OFFICIAL_INTRADAY")
        self.assertIsNone(row.error)
        self.assertIsInstance(row.quote_time, datetime)
        self.assertEqual(row.quote_time.tzinfo, TZ)

    def test_empty_header_is_explicit(self) -> None:
        row = parse_csindex_intraday_payload(
            {"code": "200", "data": {"intraDayHeader": None}},
            index_code="CSPSADRP",
        )
        self.assertEqual(row.error, "NO_INDEX_QUOTE")

    def test_supported_code_patterns(self) -> None:
        for code in ("930875", "931136", "932000", "933001", "H30094"):
            with self.subTest(code=code):
                self.assertTrue(supports_csindex_official_quote(code))
        for code in ("000300", "399986", "CSPSADRP", "CBA00101", ""):
            with self.subTest(code=code):
                self.assertFalse(supports_csindex_official_quote(code))


if __name__ == "__main__":
    unittest.main()
