from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.csi_component_proxy import (
    CsiComponentWeightSet,
    CsiWeightedConstituent,
    _build_weight_set,
    parse_csi_material_urls,
    reconstruct_csi_component_quotes,
)
from runtime.lof.index_quote import IndexQuote


TZ = ZoneInfo("Asia/Shanghai")


class LofCsiComponentProxyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 30, 10, 30, tzinfo=TZ)
        self.weight_set = CsiComponentWeightSet(
            index_code="930721",
            index_name="CS智汽车",
            weight_date=date(2026, 9, 14),
            constituent_date=date(2026, 9, 29),
            constituents=(
                CsiWeightedConstituent(
                    code="000001",
                    symbol="sz000001",
                    weight=Decimal("60"),
                ),
                CsiWeightedConstituent(
                    code="600000",
                    symbol="sh600000",
                    weight=Decimal("40"),
                ),
            ),
            total_weight=Decimal("100"),
            weight_url="https://example/weight.xls",
            constituent_url="https://example/cons.xls",
            fetched_at=self.now,
        )

    def test_parse_material_urls(self) -> None:
        payload = {
            "data": {
                "样本权重": [{"filePath": "https://example/w.xls"}],
                "样本列表": [{"filePath": "https://example/c.xls"}],
            }
        }
        self.assertEqual(
            parse_csi_material_urls(payload),
            ("https://example/w.xls", "https://example/c.xls"),
        )

    def test_build_weight_set_requires_same_constituents(self) -> None:
        header = [""] * 10
        weight_rows = [
            header,
            ["20260914", "930721", "CS智汽车", "", "000001", "", "", "深圳证券交易所", "", 60],
            ["20260914", "930721", "CS智汽车", "", "600000", "", "", "上海证券交易所", "", 40],
        ]
        constituent_rows = [
            header,
            ["20260929", "930721", "CS智汽车", "", "000001"],
            ["20260929", "930721", "CS智汽车", "", "600000"],
        ]
        row = _build_weight_set(
            index_code="930721",
            weight_rows=weight_rows,
            constituent_rows=constituent_rows,
            weight_url="w",
            constituent_url="c",
            fetched_at=self.now,
        )
        self.assertTrue(row.available)
        self.assertEqual(row.total_weight, Decimal("100"))

        constituent_rows[-1][4] = "600001"
        mismatch = _build_weight_set(
            index_code="930721",
            weight_rows=weight_rows,
            constituent_rows=constituent_rows,
            weight_url="w",
            constituent_url="c",
            fetched_at=self.now,
        )
        self.assertFalse(mismatch.available)
        self.assertEqual(
            mismatch.error,
            "CSI_CONSTITUENT_WEIGHT_SET_MISMATCH",
        )

    @patch("runtime.lof.csi_component_proxy.fetch_tencent_index_quotes")
    def test_reconstructs_weighted_intraday_return(self, quote_mock) -> None:
        quote_mock.return_value = {
            "sz000001": IndexQuote(
                symbol="sz000001",
                code="000001",
                name="A",
                current=Decimal("11"),
                previous_close=Decimal("10"),
                quote_time=self.now,
                source="TENCENT_QUOTE",
            ),
            "sh600000": IndexQuote(
                symbol="sh600000",
                code="600000",
                name="B",
                current=Decimal("9.5"),
                previous_close=Decimal("10"),
                quote_time=self.now,
                source="TENCENT_QUOTE",
            ),
        }
        result = reconstruct_csi_component_quotes(
            {"930721": self.weight_set},
            as_of=self.now,
        )["930721"]
        self.assertIsNone(result.error)
        self.assertEqual(result.source, "CSI_COMPONENT_WEIGHT_PROXY")
        self.assertEqual(result.previous_close, Decimal("1"))
        self.assertEqual(result.current, Decimal("1.04"))

    @patch("runtime.lof.csi_component_proxy.fetch_tencent_index_quotes")
    def test_low_quote_coverage_fails_closed(self, quote_mock) -> None:
        quote_mock.return_value = {
            "sz000001": IndexQuote(
                symbol="sz000001",
                code="000001",
                name="A",
                current=Decimal("11"),
                previous_close=Decimal("10"),
                quote_time=self.now,
                source="TENCENT_QUOTE",
            )
        }
        result = reconstruct_csi_component_quotes(
            {"930721": self.weight_set},
            as_of=self.now,
        )["930721"]
        self.assertEqual(
            result.error,
            "CSI_COMPONENT_QUOTE_COVERAGE_TOO_LOW",
        )

    @patch("runtime.lof.csi_component_proxy.fetch_tencent_index_quotes")
    def test_stale_weight_fails_closed(self, quote_mock) -> None:
        quote_mock.return_value = {}
        stale = CsiComponentWeightSet(
            **{
                **self.weight_set.__dict__,
                "weight_date": date(2026, 7, 1),
            }
        )
        result = reconstruct_csi_component_quotes(
            {"930721": stale},
            as_of=self.now,
        )["930721"]
        self.assertEqual(result.error, "CSI_COMPONENT_WEIGHT_STALE")


if __name__ == "__main__":
    unittest.main()