from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.india_index_quote import (
    DelayedGlobalIndexQuote,
    parse_eastmoney_delayed_global_index,
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
        source="EASTMONEY_PUSH2DELAY",
        error=None,
    )


def test_parse_delayed_sensex_quote_and_timestamp():
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
    assert quote.error is None
    assert quote.current == Decimal("71909.7")
    assert quote.previous_close == Decimal("72480.29")
    assert quote.quote_time == quote_time
    assert quote.session_return is not None
    assert quote.session_return < 0


def test_india_cash_timing_regime_matches_china_clock():
    assert india_cash_timing_regime(
        datetime(2026, 9, 30, 10, 30, tzinfo=SHANGHAI_TZ)
    ) == "INDIA_CASH_NOT_OPEN"
    assert india_cash_timing_regime(
        datetime(2026, 9, 30, 14, 0, tzinfo=SHANGHAI_TZ)
    ) == "INDIA_CASH_OPEN"


def test_morning_does_not_fake_live_india_nav():
    as_of = datetime(2026, 9, 30, 10, 30, tzinfo=SHANGHAI_TZ)
    quote = _quote(
        quote_time=datetime(2026, 9, 30, 10, 25, tzinfo=SHANGHAI_TZ)
    )
    result = resolve_164824_india_shadow(
        official_nav=Decimal("1.2000"),
        quote=quote,
        as_of=as_of,
    )
    assert result.shadow_status == "UNAVAILABLE"
    assert result.error == "INDIA_CASH_NOT_OPEN"
    assert result.estimated_nav is None
    assert result.eligible_for_main is False


def test_afternoon_fresh_delayed_quote_can_run_shadow_only():
    as_of = datetime(2026, 9, 30, 14, 50, tzinfo=SHANGHAI_TZ)
    quote = _quote(
        quote_time=datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ)
    )
    result = resolve_164824_india_shadow(
        official_nav=Decimal("1.2000"),
        quote=quote,
        as_of=as_of,
    )
    assert result.shadow_status == "AVAILABLE"
    assert result.shadow_quality == "LOW"
    assert result.estimated_nav == Decimal("1.212000")
    assert result.quote_age_seconds == 600
    assert result.eligible_for_main is False


def test_prior_session_quote_is_stale_on_india_holiday():
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
    assert result.shadow_status == "STALE"
    assert result.shadow_quality == "LOW"
    assert result.error == "STALE_SENSEX_QUOTE"
    assert result.estimated_nav is None
    assert result.quote_age_seconds > 1800


def test_missing_delayed_quote_fails_closed():
    quote = parse_eastmoney_delayed_global_index(
        {"data": {"diff": []}},
        secid="100.SENSEX",
    )
    result = resolve_164824_india_shadow(
        official_nav=Decimal("1.2378"),
        quote=quote,
        as_of=datetime(2026, 9, 30, 14, 0, tzinfo=SHANGHAI_TZ),
    )
    assert result.shadow_status == "UNAVAILABLE"
    assert result.shadow_quality == "UNKNOWN"
    assert result.error == "NO_QUOTE"
    assert result.eligible_for_main is False
