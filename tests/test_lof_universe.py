from __future__ import annotations

import unittest

from runtime.lof.universe import (
    fetch_all_lof_universe,
    parse_sse_universe,
    parse_szse_universe_page,
)


class LofUniverseParserTest(unittest.TestCase):
    def test_parse_sse_universe(self) -> None:
        payload = {
            "result": [
                {
                    "fundCode": "501001",
                    "secNameFull": "财通精选混合LOF",
                    "companyName": "财通基金管理有限公司",
                    "listingDate": "20150925",
                },
                {
                    "fundCode": "",
                    "secNameFull": "坏数据",
                },
            ]
        }
        rows = parse_sse_universe(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "501001")
        self.assertEqual(rows[0].exchange, "SSE")
        self.assertEqual(rows[0].source, "SSE_OFFICIAL")

    def test_parse_szse_universe_page_strips_html(self) -> None:
        payload = [
            {
                "metadata": {"pagecount": 1},
                "data": [
                    {
                        "sys_key": "<a><u>161128</u></a>",
                        "kzjcurl": "<a><u>标普信息科技LOF</u></a>",
                        "glrmc": "易方达基金管理有限公司",
                    }
                ],
            }
        ]
        rows = parse_szse_universe_page(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "161128")
        self.assertEqual(rows[0].name, "标普信息科技LOF")
        self.assertEqual(rows[0].exchange, "SZSE")
        self.assertEqual(rows[0].source, "SZSE_OFFICIAL")

    def test_empty_payload_is_safe(self) -> None:
        self.assertEqual(parse_szse_universe_page([]), [])

    def test_fetch_all_is_not_unit_tested_against_live_network(self) -> None:
        # Keep CI deterministic. Live-source smoke tests belong to deployment /
        # runtime preflight, not unit tests.
        self.assertTrue(callable(fetch_all_lof_universe))


if __name__ == "__main__":
    unittest.main()
