from __future__ import annotations

import unittest

from runtime.lof.classification import FundTypeRecord
from runtime.lof.mapping import ResolverMappingCandidate
from runtime.lof.resolver_classification import classify_resolver


class LofResolverClassificationTest(unittest.TestCase):
    def _type(self, code: str, name: str, raw: str) -> FundTypeRecord:
        return FundTypeRecord(
            code=code,
            name_raw=name,
            fund_type_raw=raw,
            lof_type="OTHER",
            source="TEST",
        )

    def _mapping(self, code: str, target: str | None) -> ResolverMappingCandidate:
        return ResolverMappingCandidate(
            fund_code=code,
            tracking_target_name=target,
            benchmark_text=None,
            exposure_ratio_candidate=None,
            source="TEST",
        )

    def test_domestic_index(self) -> None:
        row = classify_resolver(
            fund_code="163407",
            fund_name="兴全沪深300指数(LOF)A",
            fund_type=self._type("163407", "", "指数型-股票"),
            mapping=self._mapping("163407", "沪深300指数"),
        )
        self.assertEqual(row.resolver_class, "R1_DOMESTIC_INDEX")

    def test_qdii_index(self) -> None:
        row = classify_resolver(
            fund_code="161128",
            fund_name="标普信息科技指数(QDII-LOF)",
            fund_type=self._type("161128", "", "指数型-海外股票"),
            mapping=self._mapping("161128", "标普500信息科技指数"),
        )
        self.assertEqual(row.resolver_class, "R3_QDII_INDEX")

    def test_cross_border_non_qdii_is_not_domestic_r1(self) -> None:
        row = classify_resolver(
            fund_code="501301",
            fund_name="华宝港股通恒生中国30ETF联接(LOF)A",
            fund_type=self._type("501301", "", "指数型-股票"),
            mapping=self._mapping("501301", "恒生中国(香港上市)30指数"),
        )
        self.assertEqual(row.resolver_class, "R5_SPECIAL")
        self.assertEqual(row.reason, "cross_border_non_qdii")

    def test_commodity_is_special(self) -> None:
        row = classify_resolver(
            fund_code="161715",
            fund_name="招商大宗商品(LOF)",
            fund_type=self._type("161715", "", "指数型-股票"),
            mapping=self._mapping("161715", "大宗商品指数"),
        )
        self.assertEqual(row.resolver_class, "R5_SPECIAL")


if __name__ == "__main__":
    unittest.main()
