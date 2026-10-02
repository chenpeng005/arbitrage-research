import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.india_index_quote import DelayedGlobalIndexQuote
from runtime.lof.r4_india_fx import resolve_inr_cny_bridge
from runtime.lof.r4_india_shadow_runner import calculate_shadow_row


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _quote(
    code: str,
    current: str,
    previous_close: str,
    quote_time: datetime,
) -> DelayedGlobalIndexQuote:
    return DelayedGlobalIndexQuote(
        secid=code,
        code=code.split(".", 1)[0],
        name=code,
        current=Decimal(current),
        previous_close=Decimal(previous_close),
        quote_time=quote_time,
        source="WSCN_MARKET_REAL",
        error=None,
    )


class R4IndiaFxBridgeTests(unittest.TestCase):
    def test_inr_cny_bridge_is_cross_rate_return(self):
        as_of = datetime(2026, 9, 30, 14, 50, tzinfo=SHANGHAI_TZ)
        usd_inr = _quote(
            "USDINR.OTC",
            "96.00",
            "95.50",
            datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ),
        )
        usd_cny = _quote(
            "USDCNY.OTC",
            "6.7000",
            "6.7200",
            datetime(2026, 9, 30, 14, 41, tzinfo=SHANGHAI_TZ),
        )
        result = resolve_inr_cny_bridge(
            usd_inr=usd_inr,
            usd_cny=usd_cny,
            as_of=as_of,
        )
        expected = (
            (Decimal("6.7000") / Decimal("96.00"))
            / (Decimal("6.7200") / Decimal("95.50"))
            - Decimal("1")
        )
        self.assertEqual(result.status, "AVAILABLE")
        self.assertEqual(result.quality, "LOW")
        self.assertEqual(result.fx_return, expected)
        self.assertEqual(result.max_quote_age_seconds, 600)

    def test_stale_fx_fails_closed(self):
        as_of = datetime(2026, 10, 2, 14, 0, tzinfo=SHANGHAI_TZ)
        old = datetime(2026, 10, 1, 14, 0, tzinfo=SHANGHAI_TZ)
        result = resolve_inr_cny_bridge(
            usd_inr=_quote("USDINR.OTC", "96", "95", old),
            usd_cny=_quote("USDCNY.OTC", "6.70", "6.71", old),
            as_of=as_of,
        )
        self.assertEqual(result.status, "STALE")
        self.assertEqual(result.error, "STALE_FX_QUOTE")
        self.assertIsNone(result.fx_return)

    def test_runner_keeps_parallel_sensex_and_fx_estimates(self):
        as_of = datetime(2026, 9, 30, 14, 50, tzinfo=SHANGHAI_TZ)
        sensex = _quote(
            "SENSEX.OTC",
            "101",
            "100",
            datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ),
        )
        usd_inr = _quote(
            "USDINR.OTC",
            "96.00",
            "95.50",
            datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ),
        )
        usd_cny = _quote(
            "USDCNY.OTC",
            "6.7000",
            "6.7200",
            datetime(2026, 9, 30, 14, 41, tzinfo=SHANGHAI_TZ),
        )
        main_snapshot = {
            "snapshot_id": "main-x",
            "rows": [{
                "code": "164824",
                "name": "印度基金LOF",
                "price": 1.30,
                "quote_status": "FRESH",
                "quote_time": "2026-09-30T14:50:00+08:00",
                "official_nav": 1.20,
                "official_nav_date": "2026-09-29",
            }],
        }
        row = calculate_shadow_row(
            main_snapshot=main_snapshot,
            quote=sensex,
            as_of=as_of,
            usd_inr_quote=usd_inr,
            usd_cny_quote=usd_cny,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertEqual(row["fx_bridge_status"], "AVAILABLE")
        self.assertAlmostEqual(
            row["shadow_estimated_nav_sensex"],
            1.212,
            places=9,
        )
        self.assertIsNotNone(row["shadow_estimated_nav_sensex_inr_cny"])
        self.assertNotEqual(
            row["shadow_estimated_nav_sensex"],
            row["shadow_estimated_nav_sensex_inr_cny"],
        )
        self.assertFalse(row["eligible_for_main"])


if __name__ == "__main__":
    unittest.main()
