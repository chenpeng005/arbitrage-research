from __future__ import annotations

import unittest

from runtime.lof.classification import (
    normalize_lof_type,
    parse_fundcode_search_js,
)


class LofClassificationTest(unittest.TestCase):
    def test_qdii_detection_uses_name_and_raw_type(self) -> None:
        self.assertEqual(
            normalize_lof_type(
                name="易方达标普信息科技指数(QDII-LOF)A(人民币)",
                fund_type_raw="指数型-海外股票",
            ),
            "QDII_EQUITY",
        )
        self.assertEqual(
            normalize_lof_type(
                name="国泰大宗商品(QDII-LOF)A",
                fund_type_raw="QDII-商品",
            ),
            "QDII_COMMODITY",
        )

    def test_domestic_types(self) -> None:
        self.assertEqual(
            normalize_lof_type(
                name="兴全沪深300指数(LOF)A",
                fund_type_raw="指数型-股票",
            ),
            "EQUITY",
        )
        self.assertEqual(
            normalize_lof_type(
                name="鹏华丰锐债券LOF",
                fund_type_raw="债券型-混合二级",
            ),
            "BOND",
        )
        self.assertEqual(
            normalize_lof_type(
                name="易方达中债新综指发起式(LOF)A",
                fund_type_raw="指数型-固收",
            ),
            "BOND",
        )
        self.assertEqual(
            normalize_lof_type(
                name="某FOF-LOF",
                fund_type_raw="FOF-稳健型",
            ),
            "FOF",
        )

    def test_parse_js_payload(self) -> None:
        text = (
            'var r = [['
            '"161128","PY","易方达标普信息科技指数(QDII-LOF)A(人民币)",'
            '"指数型-海外股票","FULL"]];'
        )
        rows = parse_fundcode_search_js(text)
        self.assertEqual(rows["161128"].lof_type, "QDII_EQUITY")
        self.assertEqual(rows["161128"].fund_type_raw, "指数型-海外股票")

    def test_unknown_is_conservative(self) -> None:
        self.assertEqual(
            normalize_lof_type(name="未知LOF", fund_type_raw="其他"),
            "OTHER",
        )


if __name__ == "__main__":
    unittest.main()
