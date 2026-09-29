from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.index_proxy import parse_index_suggest_payload
from runtime.lof.index_quote_xueqiu import parse_xueqiu_index_quote


TZ = ZoneInfo("Asia/Shanghai")


class LofIndexQuoteFallbackTest(unittest.TestCase):
    def test_930_series_gets_xueqiu_symbol(self) -> None:
        payload = {
            "QuotationCodeTable": {
                "Data": [
                    {
                        "Code": "930606",
                        "Name": "中证钢铁",
                        "SecurityTypeName": "指数",
                        "MktNum": "2",
                        "QuoteID": "2.930606",
                    }
                ]
            }
        }
        row = parse_index_suggest_payload(
            payload,
            tracking_target_name="中证钢铁指数",
        )
        self.assertEqual(row.status, "RESOLVED")
        self.assertIsNone(row.tencent_symbol)
        self.assertEqual(row.xueqiu_symbol, "CSI930606")

    def test_parse_xueqiu_quote(self) -> None:
        payload = {
            "data": [
                {
                    "symbol": "CSI930606",
                    "current": 874.5331,
                    "last_close": 867.0482,
                    "timestamp": 1790657178000,
                }
            ],
            "error_code": 0,
        }
        row = parse_xueqiu_index_quote(
            payload,
            symbol="CSI930606",
        )
        self.assertEqual(row.code, "930606")
        self.assertEqual(row.current, Decimal("874.5331"))
        self.assertEqual(row.previous_close, Decimal("867.0482"))
        self.assertEqual(row.source, "XUEQIU_QUOTE")
        self.assertIsInstance(row.quote_time, datetime)
        self.assertEqual(row.quote_time.tzinfo, TZ)

    def test_empty_payload_is_explicit(self) -> None:
        row = parse_xueqiu_index_quote(
            {"data": []},
            symbol="CSI930606",
        )
        self.assertEqual(row.error, "NO_INDEX_QUOTE")


if __name__ == "__main__":
    unittest.main()
