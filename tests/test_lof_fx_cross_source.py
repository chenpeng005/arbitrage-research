from datetime import date, datetime
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from runtime.lof.fx import FxDailyClose
from runtime.lof.fx_cross_source import (
    build_usdcny_cross_source_bridge,
    calibrate_usdcny_sources,
    parse_wscn_daily_fx,
)
from runtime.lof.wscn_market_proxy import WscnMarketProxyQuote


TZ = ZoneInfo("Asia/Shanghai")


class FxCrossSourceTests(unittest.TestCase):
    def test_parse_wscn_daily_fx_uses_field_names(self):
        payload = {
            "data": {
                "fields": ["close_px", "tick_at"],
                "candle": {
                    "USDCNY.OTC": {
                        "lines": [[6.705, 1790726400]],
                    }
                },
            }
        }
        rows = parse_wscn_daily_fx(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].close, Decimal("6.705"))

    def test_calibration_passes_close_same_instrument_series(self):
        tencent = [
            FxDailyClose(date(2026, 1, i + 1), Decimal("7.0000"))
            for i in range(30)
        ]
        wscn = [
            FxDailyClose(date(2026, 1, i + 1), Decimal("7.0010"))
            for i in range(30)
        ]
        result = calibrate_usdcny_sources(
            tencent_rows=tencent,
            wscn_rows=wscn,
        )
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.common_count, 30)
        self.assertLess(result.p90_abs_level_diff_bps, 20)

    def test_calibration_fails_wide_basis(self):
        tencent = [
            FxDailyClose(date(2026, 1, i + 1), Decimal("7.00"))
            for i in range(30)
        ]
        wscn = [
            FxDailyClose(date(2026, 1, i + 1), Decimal("7.10"))
            for i in range(30)
        ]
        result = calibrate_usdcny_sources(
            tencent_rows=tencent,
            wscn_rows=wscn,
        )
        self.assertEqual(result.status, "FAIL")

    def test_fresh_wscn_current_builds_bridge(self):
        tencent = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.7045")),
        ] * 30
        calibration = type("Calibration", (), {
            "status": "PASS",
            "error": None,
        })()
        now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
        quote = WscnMarketProxyQuote(
            "USDCNY.OTC",
            "USDCNY",
            "在岸人民币",
            Decimal("6.7200"),
            Decimal("6.7100"),
            now,
            "WSCN_MARKET_REAL",
            None,
        )
        bridge = build_usdcny_cross_source_bridge(
            nav_date=date(2026, 9, 30),
            tencent_rows=tencent,
            wscn_quote=quote,
            calibration=calibration,
            as_of=now,
        )
        self.assertEqual(bridge.status, "AVAILABLE")
        self.assertIsNotNone(bridge.fx_return)

    def test_stale_wscn_current_stays_stale(self):
        calibration = type("Calibration", (), {
            "status": "PASS",
            "error": None,
        })()
        now = datetime(2026, 10, 2, 14, 0, tzinfo=TZ)
        quote = WscnMarketProxyQuote(
            "USDCNY.OTC",
            "USDCNY",
            "在岸人民币",
            Decimal("6.7050"),
            Decimal("6.7050"),
            datetime(2026, 10, 1, 3, 0, tzinfo=TZ),
            "WSCN_MARKET_REAL",
            None,
        )
        bridge = build_usdcny_cross_source_bridge(
            nav_date=date(2026, 9, 30),
            tencent_rows=[
                FxDailyClose(date(2026, 9, 30), Decimal("6.7045"))
            ],
            wscn_quote=quote,
            calibration=calibration,
            as_of=now,
        )
        self.assertEqual(bridge.status, "STALE")
        self.assertEqual(bridge.error, "STALE_WSCN_USDCNY")


if __name__ == "__main__":
    unittest.main()
