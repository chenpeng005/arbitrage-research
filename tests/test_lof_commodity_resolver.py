from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from runtime.lof.commodity_resolver import resolve_r5_commodity_bridge

TZ = ZoneInfo("Asia/Shanghai")


class CommodityResolverTest(unittest.TestCase):
    def test_effective_time_uses_older_of_commodity_and_fx(self) -> None:
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        commodity_time = now - timedelta(seconds=30)
        fx_time = now - timedelta(seconds=240)
        result = resolve_r5_commodity_bridge(
            fund_code="160719",
            official_nav=Decimal("1"),
            official_nav_date=date(2026, 9, 29),
            commodity_anchor=Decimal("100"),
            commodity_current=Decimal("101"),
            commodity_proxy_id="GC00Y",
            commodity_quote_time=commodity_time,
            fx_anchor=Decimal("7"),
            fx_current=Decimal("7.01"),
            fx_quote_time=fx_time,
            as_of=now,
            exposure_ratio=Decimal("1"),
            proxy_quality="MEDIUM",
            max_proxy_age_seconds=180,
        )
        self.assertEqual(result.estimated_nav_status, "STALE")
        self.assertEqual(result.estimated_nav_time, fx_time)
        self.assertEqual(result.proxy_time, commodity_time)
        self.assertEqual(result.fx_time, fx_time)

    def test_fresh_commodity_and_fx_are_available(self) -> None:
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        result = resolve_r5_commodity_bridge(
            fund_code="160719",
            official_nav=Decimal("1"),
            official_nav_date=date(2026, 9, 29),
            commodity_anchor=Decimal("100"),
            commodity_current=Decimal("101"),
            commodity_proxy_id="GC00Y",
            commodity_quote_time=now - timedelta(seconds=20),
            fx_anchor=Decimal("7"),
            fx_current=Decimal("7.01"),
            fx_quote_time=now - timedelta(seconds=10),
            as_of=now,
            exposure_ratio=Decimal("1"),
            proxy_quality="MEDIUM",
            max_proxy_age_seconds=180,
        )
        self.assertEqual(result.estimated_nav_status, "AVAILABLE")
        self.assertEqual(result.estimated_nav_time, now - timedelta(seconds=20))


if __name__ == "__main__":
    unittest.main()
