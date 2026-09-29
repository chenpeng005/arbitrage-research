from __future__ import annotations

import unittest

from runtime.lof.index_proxy import parse_index_suggest_payload


class LofIndexProxyMappingTest(unittest.TestCase):
    def test_exact_index_name_prefers_sse_canonical_duplicate(self) -> None:
        payload = {
            "QuotationCodeTable": {
                "Data": [
                    {
                        "Code": "399300",
                        "Name": "沪深300",
                        "SecurityTypeName": "指数",
                        "MktNum": "0",
                        "QuoteID": "0.399300",
                    },
                    {
                        "Code": "000300",
                        "Name": "沪深300",
                        "SecurityTypeName": "指数",
                        "MktNum": "1",
                        "QuoteID": "1.000300",
                    },
                ]
            }
        }
        row = parse_index_suggest_payload(
            payload,
            tracking_target_name="沪深300指数",
        )
        self.assertEqual(row.status, "RESOLVED")
        self.assertEqual(row.index_code, "000300")
        self.assertEqual(row.tencent_symbol, "sh000300")

    def test_csi_custom_index_can_resolve_without_tencent_symbol(self) -> None:
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
        self.assertEqual(row.index_code, "930606")
        self.assertIsNone(row.tencent_symbol)

    def test_no_exact_match_fails_closed(self) -> None:
        row = parse_index_suggest_payload(
            {"QuotationCodeTable": {"Data": []}},
            tracking_target_name="不存在指数",
        )
        self.assertEqual(row.status, "UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
