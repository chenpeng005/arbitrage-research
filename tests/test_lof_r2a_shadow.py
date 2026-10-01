from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.r2a_holdings import (
    Holding,
    HoldingsSnapshot,
    HoldingsStore,
    discover_periods,
    fetch_latest_full_snapshot,
    snapshot_identity,
)
from runtime.lof.r2a_shadow import (
    LiveQuote,
    PROFILES,
    calculate_rows,
    holdings_need_refresh,
    parse_quote_response,
)


TZ = ZoneInfo("Asia/Shanghai")


def _holdings(code: str, positions: list[Holding], now: datetime):
    rows = tuple(positions)
    return HoldingsSnapshot(
        fund_code=code,
        as_of_date=date(2026, 6, 30),
        first_seen_at=now - timedelta(days=20),
        fetched_at=now,
        holdings=rows,
        total_weight=sum(
            (x.nav_weight for x in rows),
            Decimal("0"),
        ),
        identity=snapshot_identity(
            code,
            date(2026, 6, 30),
            rows,
        ),
    )


def _market_row(code: str, lag: str = "T-1"):
    return {
        "code": code,
        "name": code,
        "official_nav": 1.0,
        "official_nav_date": "2026-09-30",
        "official_nav_lag_label": lag,
        "price": 1.05,
    }


class R2AShadowTest(unittest.TestCase):
    def test_quote_parser_reads_current_prev_close_and_time(self):
        fields = ["0"] * 40
        fields[3] = "110"
        fields[4] = "100"
        fields[30] = "20260930103000"
        rows = parse_quote_response(
            'v_sz000001="' + "~".join(fields) + '";'
        )
        q = rows["sz000001"]
        self.assertTrue(q.available)
        self.assertEqual(q.current, Decimal("110"))
        self.assertEqual(q.previous_close, Decimal("100"))

    @patch("runtime.lof.r2a_holdings._request_text")
    def test_discovery_falls_back_to_year(self, request_mock):
        request_mock.side_effect = [
            "no period",
            "截止至：<font>2026-06-30</font>",
        ]
        periods = discover_periods(
            "163110",
            as_of=date(2026, 10, 1),
        )
        self.assertEqual(periods, [date(2026, 6, 30)])
        self.assertEqual(request_mock.call_count, 2)

    @patch("runtime.lof.r2a_holdings._request_text")
    def test_discovery_falls_back_to_explicit_quarter(
        self,
        request_mock,
    ):
        request_mock.side_effect = [
            "no period",
            "no period",
            "no period",
            "截止至：<font>2026-09-30</font>",
        ]
        periods = discover_periods(
            "163110",
            as_of=date(2026, 10, 1),
        )
        self.assertEqual(periods, [date(2026, 9, 30)])
        self.assertEqual(request_mock.call_count, 4)

    @patch("runtime.lof.r2a_holdings.fetch_period_holdings")
    @patch("runtime.lof.r2a_holdings.discover_periods")
    def test_latest_partial_period_falls_back_to_full(
        self,
        periods_mock,
        fetch_mock,
    ):
        periods_mock.return_value = [
            date(2026, 9, 30),
            date(2026, 6, 30),
        ]
        fetch_mock.side_effect = [
            (
                Holding(
                    "A","sz000001","000001","A",Decimal("0.30")
                ),
            ),
            (
                Holding(
                    "A","sz000001","000001","A",Decimal("0.45")
                ),
                Holding(
                    "A","sh600000","600000","B",Decimal("0.40")
                ),
            ),
        ]
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        row = fetch_latest_full_snapshot(
            "501201",
            now=now,
        )
        self.assertEqual(row.as_of_date, date(2026, 6, 30))
        self.assertEqual(row.total_weight, Decimal("0.85"))

    def test_a_share_basket_available(self):
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        hs = _holdings(
            "501201",
            [
                Holding(
                    "A","sz000001","000001","A",Decimal("0.50")
                ),
                Holding(
                    "A","sh600000","600000","B",Decimal("0.35")
                ),
            ],
            now,
        )
        rows = calculate_rows(
            main_snapshot={"rows":[_market_row("501201")]},
            holdings_by_fund={"501201":hs},
            quotes={
                "sz000001": LiveQuote(
                    "sz000001",Decimal("110"),Decimal("100"),now
                ),
                "sh600000": LiveQuote(
                    "sh600000",Decimal("90"),Decimal("100"),now
                ),
            },
            as_of=now,
        )
        row = next(x for x in rows if x["fund_code"]=="501201")
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(row["estimated_return"], 0.015, places=8)
        self.assertAlmostEqual(row["shadow_estimated_nav"], 1.015, places=8)

    def test_t2_nav_fails_closed(self):
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        hs = _holdings(
            "501201",
            [
                Holding(
                    "A","sz000001","000001","A",Decimal("0.85")
                ),
            ],
            now,
        )
        rows = calculate_rows(
            main_snapshot={"rows":[_market_row("501201","T-2")]},
            holdings_by_fund={"501201":hs},
            quotes={},
            as_of=now,
        )
        row = next(x for x in rows if x["fund_code"]=="501201")
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["error"], "OFFICIAL_NAV_NOT_T1")

    @patch("runtime.lof.r2a_holdings.fetch_latest_full_snapshot")
    def test_refresh_failure_preserves_previous_snapshot(
        self,
        fetch_mock,
    ):
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        row = _holdings(
            "501219",
            [
                Holding(
                    "A","sz000001","000001","A",Decimal("0.85")
                ),
            ],
            now - timedelta(hours=1),
        )
        fetch_mock.side_effect = RuntimeError("temporary source failure")
        with tempfile.TemporaryDirectory() as tmp:
            store = HoldingsStore(Path(tmp))
            store.persist(
                {"501219": row},
                generated_at=now - timedelta(hours=1),
            )
            loaded, errors = store.refresh(
                ["501219"],
                now=now,
                timeout=1,
            )
        self.assertIn("501219", loaded)
        self.assertEqual(
            loaded["501219"].identity,
            row.identity,
        )
        self.assertIn("501219", errors)

    def test_holdings_cache_refresh_gate(self):
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        base = _holdings(
            "501201",
            [
                Holding(
                    "A","sz000001","000001","A",Decimal("0.85")
                ),
            ],
            now,
        )
        complete = {}
        for code in PROFILES:
            complete[code] = HoldingsSnapshot(
                fund_code=code,
                as_of_date=base.as_of_date,
                first_seen_at=base.first_seen_at,
                fetched_at=now - timedelta(hours=1),
                holdings=base.holdings,
                total_weight=base.total_weight,
                identity=base.identity + code,
            )
        self.assertFalse(
            holdings_need_refresh(
                complete,
                now=now,
                refresh_seconds=21600,
            )
        )
        missing = dict(complete)
        missing.pop("501219")
        self.assertTrue(
            holdings_need_refresh(
                missing,
                now=now,
                refresh_seconds=21600,
            )
        )
        stale = dict(complete)
        old = stale["501219"]
        stale["501219"] = HoldingsSnapshot(
            fund_code=old.fund_code,
            as_of_date=old.as_of_date,
            first_seen_at=old.first_seen_at,
            fetched_at=now - timedelta(hours=7),
            holdings=old.holdings,
            total_weight=old.total_weight,
            identity=old.identity,
        )
        self.assertTrue(
            holdings_need_refresh(
                stale,
                now=now,
                refresh_seconds=21600,
            )
        )

    def test_hk_return_includes_fx(self):
        now = datetime(2026, 10, 1, 10, 0, tzinfo=TZ)
        hs = _holdings(
            "160127",
            [
                Holding(
                    "A","sz000001","000001","A",Decimal("0.40")
                ),
                Holding(
                    "HK","hk01070","01070","H",Decimal("0.45")
                ),
            ],
            now,
        )
        rows = calculate_rows(
            main_snapshot={"rows":[_market_row("160127")]},
            holdings_by_fund={"160127":hs},
            quotes={
                "sz000001": LiveQuote(
                    "sz000001",Decimal("100"),Decimal("100"),now
                ),
                "hk01070": LiveQuote(
                    "hk01070",Decimal("10.5"),Decimal("10"),now
                ),
            },
            as_of=now,
            fx_current=Decimal("0.86"),
            fx_quote_time=now,
            fx_anchor_by_nav_date={
                date(2026,9,30):Decimal("0.85")
            },
        )
        row = next(x for x in rows if x["fund_code"]=="160127")
        expected = Decimal("0.45") * (
            Decimal("10.5")/Decimal("10")
            * Decimal("0.86")/Decimal("0.85")
            - Decimal("1")
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(
            row["estimated_return"],
            float(expected),
            places=8,
        )


if __name__ == "__main__":
    unittest.main()
