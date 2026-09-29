from __future__ import annotations

import unittest

from runtime.lof.index_proxy import (
    parse_index_suggest_payload,
    proxy_from_tracking_index_code,
)


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

    def test_price_suffix_and_alias_normalization(self) -> None:
        payload = {
            "QuotationCodeTable": {
                "Data": [
                    {
                        "Code": "399006",
                        "Name": "创业板指",
                        "SecurityTypeName": "指数",
                        "MktNum": "0",
                        "QuoteID": "0.399006",
                    }
                ]
            }
        }
        row = parse_index_suggest_payload(
            payload,
            tracking_target_name="创业板指数(价格)",
        )
        self.assertEqual(row.status, "RESOLVED")
        self.assertEqual(row.index_code, "399006")

    def test_direct_domestic_code_mapping(self) -> None:
        cases = [
            ("000300", "sh000300", "SH000300"),
            ("399998", "sz399998", "SZ399998"),
            ("930606", None, "CSI930606"),
            ("980017", "sz980017", "SZ980017"),
        ]
        for code, tencent, xueqiu in cases:
            with self.subTest(code=code):
                row = proxy_from_tracking_index_code(
                    tracking_target_name="示例指数",
                    index_code=code,
                    index_name="示例指数",
                )
                self.assertEqual(row.status, "RESOLVED")
                self.assertEqual(row.tencent_symbol, tencent)
                self.assertEqual(row.xueqiu_symbol, xueqiu)

    def test_unsupported_direct_code_fails_closed(self) -> None:
        row = proxy_from_tracking_index_code(
            tracking_target_name="恒生指数",
            index_code="HSI",
            index_name="恒生指数",
        )
        self.assertEqual(row.status, "UNRESOLVED")
        self.assertEqual(row.error, "UNSUPPORTED_INDEX_CODE_PATTERN")

    def test_no_exact_match_fails_closed(self) -> None:
        row = parse_index_suggest_payload(
            {"QuotationCodeTable": {"Data": []}},
            tracking_target_name="不存在指数",
        )
        self.assertEqual(row.status, "UNRESOLVED")


if __name__ == "__main__":
    unittest.main()
