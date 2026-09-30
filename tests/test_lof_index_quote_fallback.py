from __future__ import annotations

import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.index_proxy import IndexProxyMapping, parse_index_suggest_payload
from runtime.lof.index_quote import IndexQuote
from runtime.lof.index_quote_xueqiu import (
    fetch_index_quotes_for_mappings,
    parse_xueqiu_index_quote,
)


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

    @patch("runtime.lof.index_quote_xueqiu.fetch_xueqiu_index_quotes")
    @patch("runtime.lof.index_quote_csindex.fetch_csindex_index_quotes")
    @patch("runtime.lof.index_quote.fetch_tencent_index_quotes")
    def test_csi_official_precedes_xueqiu(
        self,
        tencent_mock,
        csindex_mock,
        xueqiu_mock,
    ) -> None:
        mapping = IndexProxyMapping(
            tracking_target_name="空天军工",
            index_code="930875",
            index_name="空天军工",
            quote_id="2.930875",
            market_num="2",
            tencent_symbol=None,
            xueqiu_symbol="CSI930875",
            source="DIRECT_TRACKING_INDEX_CODE",
            status="RESOLVED",
        )
        tencent_mock.return_value = {}
        csindex_mock.return_value = {
            "930875": IndexQuote(
                symbol="930875",
                code="930875",
                name=None,
                current=Decimal("1964.22"),
                previous_close=Decimal("1953.07"),
                quote_time=datetime(2026, 9, 30, 11, 29, 57, tzinfo=TZ),
                source="CSI_OFFICIAL_INTRADAY",
            )
        }
        xueqiu_mock.return_value = {}

        result = fetch_index_quotes_for_mappings([mapping])
        row = result[(None, "CSI930875")]

        self.assertEqual(row.source, "CSI_OFFICIAL_INTRADAY")
        self.assertEqual(row.current, Decimal("1964.22"))
        xueqiu_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
