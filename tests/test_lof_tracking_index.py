from __future__ import annotations

import unittest

from runtime.lof.tracking_index import parse_tracking_index_rows


class LofTrackingIndexMappingTest(unittest.TestCase):
    def test_parse_direct_tracking_index_code(self) -> None:
        rows = parse_tracking_index_rows(
            [
                {
                    "FCode": "160119",
                    "ShortName": "南方中证500ETF联接(LOF)A",
                    "StandarIndexCode": "000905",
                    "IndexName": "中证500",
                }
            ],
            market_bucket="zs:lof",
        )
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row.fund_code, "160119")
        self.assertEqual(row.tracking_index_code, "000905")
        self.assertEqual(row.tracking_index_name, "中证500")
        self.assertTrue(row.available)

    def test_non_numeric_overseas_index_code_is_preserved(self) -> None:
        rows = parse_tracking_index_rows(
            [
                {
                    "FCode": "161128",
                    "ShortName": "标普信息科技LOF",
                    "StandarIndexCode": "S5INFT",
                    "IndexName": "标普500信息科技指数",
                }
            ],
            market_bucket="qdii:all",
        )
        self.assertEqual(rows[0].tracking_index_code, "S5INFT")

    def test_missing_index_is_explicit(self) -> None:
        rows = parse_tracking_index_rows(
            [
                {
                    "FCode": "501225",
                    "ShortName": "全球芯片LOF",
                    "StandarIndexCode": "",
                    "IndexName": "",
                }
            ],
            market_bucket="qdii:all",
        )
        self.assertFalse(rows[0].available)
        self.assertEqual(rows[0].error, "MISSING_TRACKING_INDEX")


if __name__ == "__main__":
    unittest.main()
