from __future__ import annotations

import unittest
from decimal import Decimal

from runtime.lof.mapping import parse_f10_mapping


class LofResolverMappingTest(unittest.TestCase):
    def test_domestic_index_mapping_and_exposure(self) -> None:
        html = """
        <table>
          <tr>
            <th>业绩比较基准</th>
            <td>沪深300指数收益率*95%+银行活期存款利率(税后)*5%</td>
            <th>跟踪标的</th>
            <td>沪深300指数</td>
          </tr>
        </table>
        """
        row = parse_f10_mapping(html, fund_code="160615")
        self.assertEqual(row.tracking_target_name, "沪深300指数")
        self.assertEqual(
            row.benchmark_text,
            "沪深300指数收益率*95%+银行活期存款利率(税后)*5%",
        )
        self.assertEqual(row.exposure_ratio_candidate, Decimal("0.95"))

    def test_qdii_index_mapping_with_fx_wording(self) -> None:
        html = """
        <table>
          <tr>
            <th>业绩比较基准</th>
            <td>标普500信息科技指数收益率(使用估值汇率折算)*95%+活期存款利率(税后)*5%</td>
            <th>跟踪标的</th>
            <td>标普500信息科技指数</td>
          </tr>
        </table>
        """
        row = parse_f10_mapping(html, fund_code="161128")
        self.assertEqual(row.tracking_target_name, "标普500信息科技指数")
        self.assertEqual(row.exposure_ratio_candidate, Decimal("0.95"))

    def test_missing_mapping_is_explicit(self) -> None:
        row = parse_f10_mapping(
            "<html><body>普通主动基金</body></html>",
            fund_code="000001",
        )
        self.assertEqual(row.error, "NO_TRACKING_MAPPING_DATA")
        self.assertIsNone(row.tracking_target_name)


if __name__ == "__main__":
    unittest.main()
