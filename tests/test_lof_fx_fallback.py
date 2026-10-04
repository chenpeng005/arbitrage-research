from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.fx import FxDailyClose, FxQuote
from runtime.lof.fx_cross_source import FxCrossSourceCalibration
from runtime.lof.fx_resolver import (
    _resolve_usdcny_uncached,
)
from runtime.lof.wscn_market_proxy import WscnMarketProxyQuote


TZ = ZoneInfo("Asia/Shanghai")


def calibration(status="PASS"):
    return FxCrossSourceCalibration(
        status=status,
        common_count=120,
        common_start=date(2026, 1, 1),
        common_end=date(2026, 9, 30),
        median_abs_level_diff_bps=4.0,
        p90_abs_level_diff_bps=12.0,
        max_abs_level_diff_bps=30.0,
        error=None if status == "PASS" else "CROSS_SOURCE_BASIS_TOO_WIDE",
    )


class FxFallbackV1Test(unittest.TestCase):
    def call(self, *, now):
        return _resolve_usdcny_uncached(
            nav_date=date(2026, 9, 30),
            as_of=now,
            timeout=1,
            max_quote_age_seconds=180,
            history_count=180,
            min_common_count=60,
            max_median_abs_basis_bps=8.0,
            max_p90_abs_basis_bps=20.0,
            max_anchor_basis_bps=25.0,
        )

    @patch("runtime.lof.fx_resolver.fetch_wscn_market_proxy")
    @patch("runtime.lof.fx_resolver.calibrate_usdcny_sources")
    @patch("runtime.lof.fx_resolver.fetch_wscn_daily_fx")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_quote")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_daily")
    def test_fresh_primary_always_wins(
        self, daily, quote, cnh_daily, calibrate, cnh_quote
    ):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
        daily.return_value = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.7000"))
        ]
        quote.return_value = FxQuote(
            "whUSDCNY", Decimal("6.7100"),
            now - timedelta(seconds=20), "TENCENT_FX", None
        )

        result = self.call(now=now)

        self.assertEqual(result.status, "AVAILABLE")
        self.assertEqual(result.source, "TENCENT_USDCNY_PRIMARY")
        cnh_daily.assert_not_called()
        calibrate.assert_not_called()
        cnh_quote.assert_not_called()

    @patch("runtime.lof.fx_resolver.fetch_wscn_market_proxy")
    @patch("runtime.lof.fx_resolver.calibrate_usdcny_sources")
    @patch("runtime.lof.fx_resolver.fetch_wscn_daily_fx")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_quote")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_daily")
    def test_stale_primary_uses_fresh_calibrated_cnh_return(
        self, daily, quote, cnh_daily, calibrate, cnh_quote
    ):
        now = datetime(2026, 10, 2, 10, 0, tzinfo=TZ)
        daily.return_value = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.7000"))
        ]
        quote.return_value = FxQuote(
            "whUSDCNY", Decimal("6.7050"),
            now - timedelta(days=1), "TENCENT_FX", None
        )
        cnh_daily.return_value = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.7040"))
        ]
        calibrate.return_value = calibration()
        cnh_quote.return_value = WscnMarketProxyQuote(
            "USDCNH.OTC", "USDCNH", "离岸人民币",
            Decimal("6.7240"), Decimal("6.7000"),
            now - timedelta(seconds=15), "WSCN_MARKET_REAL", None
        )

        result = self.call(now=now)

        self.assertEqual(result.status, "AVAILABLE")
        self.assertEqual(
            result.source,
            "TENCENT_CNY_ANCHOR_WSCN_CNH_RETURN",
        )
        # normalized_current / CNY anchor == CNH current / CNH anchor
        self.assertAlmostEqual(
            float(result.current / result.anchor),
            float(Decimal("6.7240") / Decimal("6.7040")),
            places=12,
        )
        self.assertAlmostEqual(result.fallback_anchor_basis_bps, 5.970149, places=5)

    @patch("runtime.lof.fx_resolver.fetch_wscn_market_proxy")
    @patch("runtime.lof.fx_resolver.calibrate_usdcny_sources")
    @patch("runtime.lof.fx_resolver.fetch_wscn_daily_fx")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_quote")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_daily")
    def test_wide_cnh_anchor_basis_rejects_fallback_and_keeps_primary(
        self, daily, quote, cnh_daily, calibrate, cnh_quote
    ):
        now = datetime(2026, 10, 2, 10, 0, tzinfo=TZ)
        daily.return_value = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.7000"))
        ]
        primary_time = now - timedelta(days=1)
        quote.return_value = FxQuote(
            "whUSDCNY", Decimal("6.7050"),
            primary_time, "TENCENT_FX", None
        )
        cnh_daily.return_value = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.8000"))
        ]
        calibrate.return_value = calibration()
        cnh_quote.return_value = WscnMarketProxyQuote(
            "USDCNH.OTC", "USDCNH", "离岸人民币",
            Decimal("6.8100"), Decimal("6.8000"),
            now, "WSCN_MARKET_REAL", None
        )

        result = self.call(now=now)

        self.assertEqual(result.source, "TENCENT_USDCNY_PRIMARY")
        self.assertEqual(result.status, "STALE")
        self.assertEqual(result.quote_time, primary_time)

    @patch("runtime.lof.fx_resolver.fetch_wscn_market_proxy")
    @patch("runtime.lof.fx_resolver.calibrate_usdcny_sources")
    @patch("runtime.lof.fx_resolver.fetch_wscn_daily_fx")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_quote")
    @patch("runtime.lof.fx_resolver.fetch_tencent_fx_daily")
    def test_newer_cnh_can_be_selected_but_remains_stale_off_market(
        self, daily, quote, cnh_daily, calibrate, cnh_quote
    ):
        now = datetime(2026, 10, 4, 8, 0, tzinfo=TZ)
        daily.return_value = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.7045"))
        ]
        quote.return_value = FxQuote(
            "whUSDCNY", Decimal("6.7050"),
            datetime(2026, 10, 1, 3, 0, tzinfo=TZ),
            "TENCENT_FX", None
        )
        cnh_daily.return_value = [
            FxDailyClose(date(2026, 9, 30), Decimal("6.7086"))
        ]
        calibrate.return_value = calibration()
        cnh_quote.return_value = WscnMarketProxyQuote(
            "USDCNH.OTC", "USDCNH", "离岸人民币",
            Decimal("6.7061"), Decimal("6.7061"),
            datetime(2026, 10, 3, 4, 59, 58, tzinfo=TZ),
            "WSCN_MARKET_REAL", None
        )

        result = self.call(now=now)

        self.assertEqual(
            result.source,
            "TENCENT_CNY_ANCHOR_WSCN_CNH_RETURN",
        )
        self.assertEqual(result.status, "STALE")
        self.assertEqual(
            result.quote_time,
            datetime(2026, 10, 3, 4, 59, 58, tzinfo=TZ),
        )


if __name__ == "__main__":
    unittest.main()
