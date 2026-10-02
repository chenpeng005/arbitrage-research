import unittest
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.india_index_quote import (
    DelayedGlobalIndexQuote,
    parse_eastmoney_delayed_global_index,
    parse_wscn_delayed_global_index,
)
from runtime.lof.r4_india_shadow import (
    india_cash_timing_regime,
    resolve_164824_india_shadow,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _quote(
    *,
    current: str = "101",
    previous_close: str = "100",
    quote_time: datetime,
) -> DelayedGlobalIndexQuote:
    return DelayedGlobalIndexQuote(
        secid="100.SENSEX",
        code="SENSEX",
        name="印度孟买SENSEX",
        current=Decimal(current),
        previous_close=Decimal(previous_close),
        quote_time=quote_time,
        source="TEST",
        error=None,
    )


class R4IndiaShadowTests(unittest.TestCase):
    def test_parse_eastmoney_sensex_quote_and_timestamp(self):
        quote_time = datetime(2026, 10, 1, 17, 58, 42, tzinfo=SHANGHAI_TZ)
        payload = {
            "data": {
                "diff": [
                    {
                        "f2": 71909.7,
                        "f12": "SENSEX",
                        "f14": "印度孟买SENSEX",
                        "f18": 72480.29,
                        "f124": int(quote_time.timestamp()),
                    }
                ]
            }
        }
        quote = parse_eastmoney_delayed_global_index(
            payload,
            secid="100.SENSEX",
        )
        self.assertIsNone(quote.error)
        self.assertEqual(quote.current, Decimal("71909.7"))
        self.assertEqual(quote.previous_close, Decimal("72480.29"))
        self.assertEqual(quote.quote_time, quote_time)
        self.assertIsNotNone(quote.session_return)
        self.assertLess(quote.session_return, 0)

    def test_parse_wscn_sensex_quote_and_derive_prev_close(self):
        quote_time = datetime(2026, 10, 1, 17, 58, 42, tzinfo=SHANGHAI_TZ)
        payload = {
            "code": 20000,
            "data": {
                "fields": [
                    "symbol",
                    "prod_name",
                    "last_px",
                    "px_change",
                    "px_change_rate",
                    "update_time",
                    "delisting_date",
                ],
                "snapshot": {
                    "SENSEX.OTC": [
                        "SENSEX",
                        "印度孟买SENSEX指数",
                        71909.7,
                        -570.59,
                        -0.79,
                        int(quote_time.timestamp()),
                        0,
                    ]
                },
            },
        }
        quote = parse_wscn_delayed_global_index(payload)
        self.assertIsNone(quote.error)
        self.assertEqual(quote.current, Decimal("71909.7"))
        self.assertEqual(quote.previous_close, Decimal("72480.29"))
        self.assertEqual(quote.quote_time, quote_time)
        self.assertEqual(quote.source, "WSCN_MARKET_REAL")

    def test_india_cash_timing_regime_matches_china_clock(self):
        self.assertEqual(
            india_cash_timing_regime(
                datetime(2026, 9, 30, 10, 30, tzinfo=SHANGHAI_TZ)
            ),
            "INDIA_CASH_NOT_OPEN",
        )
        self.assertEqual(
            india_cash_timing_regime(
                datetime(2026, 9, 30, 14, 0, tzinfo=SHANGHAI_TZ)
            ),
            "INDIA_CASH_OPEN",
        )

    def test_morning_does_not_fake_live_india_nav(self):
        as_of = datetime(2026, 9, 30, 10, 30, tzinfo=SHANGHAI_TZ)
        quote = _quote(
            quote_time=datetime(2026, 9, 30, 10, 25, tzinfo=SHANGHAI_TZ)
        )
        result = resolve_164824_india_shadow(
            official_nav=Decimal("1.2000"),
            quote=quote,
            as_of=as_of,
        )
        self.assertEqual(result.shadow_status, "UNAVAILABLE")
        self.assertEqual(result.error, "INDIA_CASH_NOT_OPEN")
        self.assertIsNone(result.estimated_nav)
        self.assertFalse(result.eligible_for_main)

    def test_afternoon_fresh_delayed_quote_can_run_shadow_only(self):
        as_of = datetime(2026, 9, 30, 14, 50, tzinfo=SHANGHAI_TZ)
        quote = _quote(
            quote_time=datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ)
        )
        result = resolve_164824_india_shadow(
            official_nav=Decimal("1.2000"),
            quote=quote,
            as_of=as_of,
        )
        self.assertEqual(result.shadow_status, "AVAILABLE")
        self.assertEqual(result.shadow_quality, "LOW")
        self.assertEqual(result.estimated_nav, Decimal("1.212000"))
        self.assertEqual(result.quote_age_seconds, 600)
        self.assertFalse(result.eligible_for_main)

    def test_prior_session_quote_is_stale_on_india_holiday(self):
        as_of = datetime(2026, 10, 2, 14, 0, tzinfo=SHANGHAI_TZ)
        quote = _quote(
            current="71909.7",
            previous_close="72480.29",
            quote_time=datetime(2026, 10, 1, 17, 58, 42, tzinfo=SHANGHAI_TZ),
        )
        result = resolve_164824_india_shadow(
            official_nav=Decimal("1.2378"),
            quote=quote,
            as_of=as_of,
        )
        self.assertEqual(result.shadow_status, "STALE")
        self.assertEqual(result.shadow_quality, "LOW")
        self.assertEqual(result.error, "STALE_SENSEX_QUOTE")
        self.assertIsNone(result.estimated_nav)
        self.assertGreater(result.quote_age_seconds, 1800)

    def test_missing_delayed_quote_fails_closed(self):
        quote = parse_eastmoney_delayed_global_index(
            {"data": {"diff": []}},
            secid="100.SENSEX",
        )
        result = resolve_164824_india_shadow(
            official_nav=Decimal("1.2378"),
            quote=quote,
            as_of=datetime(2026, 9, 30, 14, 0, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(result.shadow_status, "UNAVAILABLE")
        self.assertEqual(result.shadow_quality, "UNKNOWN")
        self.assertEqual(result.error, "NO_QUOTE")
        self.assertFalse(result.eligible_for_main)


if __name__ == "__main__":
    unittest.main()
