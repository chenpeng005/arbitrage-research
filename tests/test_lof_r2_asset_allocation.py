import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.r2_asset_allocation import (
    is_cash_heavy_candidate,
    parse_asset_allocation,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class R2AssetAllocationTests(unittest.TestCase):
    def test_parse_latest_asset_allocation(self):
        text = (
            'x;var Data_assetAllocation = '
            '{"series":['
            '{"name":"股票占净比","data":[80.63,73.78]},'
            '{"name":"债券占净比","data":[0.32,0.35]},'
            '{"name":"现金占净比","data":[20.11,26.03]}'
            '],"categories":["2026-03-31","2026-06-30"]};/*next*/'
        )
        row = parse_asset_allocation(
            text,
            fund_code="160916",
            fetched_at=datetime(2026, 10, 2, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(row.as_of_date.isoformat(), "2026-06-30")
        self.assertEqual(row.stock_weight, Decimal("0.7378"))
        self.assertEqual(row.bond_weight, Decimal("0.0035"))
        self.assertEqual(row.cash_weight, Decimal("0.2603"))
        self.assertTrue(is_cash_heavy_candidate(row))


if __name__ == "__main__":
    unittest.main()
