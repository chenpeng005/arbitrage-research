from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from runtime.lof.r2_fund_events import (
    CashDistribution,
    DistributionSchedule,
)
from runtime.lof.r2a_shadow import LiveQuote
from runtime.lof.r2c_live_overlay import (
    FUND_CODE,
    TradeHolding,
    TradeHoldingsSnapshot,
    calculate_row,
    holdings_identity,
    parse_bond_holdings,
    parse_stock_holdings,
)


TZ = ZoneInfo("Asia/Shanghai")


def market_snapshot(lag: str = "T-1") -> dict:
    return {
        "rows": [
            {
                "code": FUND_CODE,
                "name": "工银双债LOF",
                "official_nav": 1.0,
                "official_nav_date": "2026-09-30",
                "official_nav_lag_label": lag,
                "price": 1.05,
            }
        ]
    }


def schedule(
    now: datetime,
    cash: Decimal = Decimal("0"),
) -> DistributionSchedule:
    events = ()
    if cash > 0:
        events = (
            CashDistribution(
                record_date=now.date(),
                ex_date=now.date(),
                cash_per_unit=cash,
                payment_date=now.date(),
                raw_text="test",
            ),
        )
    return DistributionSchedule(
        fund_code=FUND_CODE,
        fetched_at=now,
        events=events,
    )


def holdings(
    now: datetime,
) -> TradeHoldingsSnapshot:
    rows = (
        TradeHolding(
            "STOCK",
            "sz000001",
            "000001",
            "股票A",
            Decimal("0.10"),
        ),
        TradeHolding(
            "CONVERTIBLE",
            "sz123001",
            "123001",
            "转债A",
            Decimal("0.80"),
        ),
    )
    return TradeHoldingsSnapshot(
        fund_code=FUND_CODE,
        as_of_date=date(2026, 6, 30),
        first_seen_at=now - timedelta(days=20),
        fetched_at=now,
        holdings=rows,
        stock_weight=Decimal("0.10"),
        convertible_weight=Decimal("0.80"),
        ordinary_bond_weight=Decimal("0.03"),
        trade_weight=Decimal("0.90"),
        identity=holdings_identity(date(2026, 6, 30), rows),
    )


class R2CLiveOverlayTest(unittest.TestCase):
    def test_parsers_use_target_quarter_only(self) -> None:
        stock_html = """
        <div class='box'>截止至：<font>2026-06-30</font>
        <table><tr><td>1</td><td>000001</td><td>股票A</td>
        <td></td><td></td><td></td><td>10.00%</td><td>1</td><td>1</td></tr></table>
        </div>
        <div class='box'>截止至：<font>2026-03-31</font>
        <table><tr><td>1</td><td>600000</td><td>旧股票</td>
        <td></td><td></td><td></td><td>20.00%</td><td>1</td><td>1</td></tr></table>
        </div>
        """
        bond_html = """
        <div class='box'>截止至：<font>2026-06-30</font>
        <table>
        <tr><td>1</td><td>123001</td><td>测试转债</td><td>80.00%</td><td>1</td></tr>
        <tr><td>2</td><td>019001</td><td>测试国债</td><td>3.00%</td><td>1</td></tr>
        </table>
        </div>
        <div class='box'>截止至：<font>2026-03-31</font>
        <table><tr><td>1</td><td>113001</td><td>旧转债</td><td>20.00%</td><td>1</td></tr></table>
        </div>
        """
        period = date(2026, 6, 30)
        stocks = parse_stock_holdings(stock_html, period)
        convertibles, ordinary = parse_bond_holdings(
            bond_html,
            period,
        )
        self.assertEqual(len(stocks), 1)
        self.assertEqual(stocks[0].symbol, "sz000001")
        self.assertEqual(stocks[0].nav_weight, Decimal("0.10"))
        self.assertEqual(len(convertibles), 1)
        self.assertEqual(convertibles[0].symbol, "sz123001")
        self.assertEqual(
            convertibles[0].nav_weight,
            Decimal("0.80"),
        )
        self.assertEqual(ordinary, Decimal("0.03"))

    def test_available_calculation(self) -> None:
        now = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)
        hs = holdings(now)
        quotes = {
            "sz000001": LiveQuote(
                "sz000001",
                Decimal("102"),
                Decimal("100"),
                now,
            ),
            "sz123001": LiveQuote(
                "sz123001",
                Decimal("101"),
                Decimal("100"),
                now,
            ),
        }
        row = calculate_row(
            main_snapshot=market_snapshot(),
            holdings=hs,
            distribution_schedule=schedule(now),
            quotes=quotes,
            as_of=now,
        )
        expected_return = (
            Decimal("0.10") * Decimal("0.02")
            + Decimal("0.80") * Decimal("0.01")
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(
            row["estimated_return"],
            float(expected_return),
            places=8,
        )
        self.assertAlmostEqual(
            row["shadow_estimated_nav"],
            float(Decimal("1") + expected_return),
            places=8,
        )

    def test_cash_distribution_is_subtracted(self) -> None:
        now = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)
        hs = holdings(now)
        quotes = {
            "sz000001": LiveQuote(
                "sz000001",
                Decimal("100"),
                Decimal("100"),
                now,
            ),
            "sz123001": LiveQuote(
                "sz123001",
                Decimal("100"),
                Decimal("100"),
                now,
            ),
        }
        row = calculate_row(
            main_snapshot=market_snapshot(),
            holdings=hs,
            distribution_schedule=schedule(
                now,
                Decimal("0.02"),
            ),
            quotes=quotes,
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(
            row["shadow_estimated_nav"],
            0.98,
            places=8,
        )
        self.assertAlmostEqual(
            row["cash_distribution_per_unit"],
            0.02,
            places=8,
        )

    def test_t2_nav_fails_closed(self) -> None:
        now = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)
        row = calculate_row(
            main_snapshot=market_snapshot("T-2"),
            holdings=holdings(now),
            distribution_schedule=schedule(now),
            quotes={},
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["error"], "OFFICIAL_NAV_NOT_T1")

    def test_low_live_coverage_fails_closed(self) -> None:
        now = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)
        hs = holdings(now)
        quotes = {
            "sz123001": LiveQuote(
                "sz123001",
                Decimal("101"),
                Decimal("100"),
                now,
            ),
        }
        row = calculate_row(
            main_snapshot=market_snapshot(),
            holdings=hs,
            distribution_schedule=schedule(now),
            quotes=quotes,
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertIn("LIVE_COVERAGE_TOO_LOW", row["error"])

    def test_stale_quote_makes_shadow_stale(self) -> None:
        now = datetime(2026, 10, 9, 10, 0, tzinfo=TZ)
        old = now - timedelta(minutes=10)
        hs = holdings(now)
        quotes = {
            "sz000001": LiveQuote(
                "sz000001",
                Decimal("102"),
                Decimal("100"),
                old,
            ),
            "sz123001": LiveQuote(
                "sz123001",
                Decimal("101"),
                Decimal("100"),
                now,
            ),
        }
        row = calculate_row(
            main_snapshot=market_snapshot(),
            holdings=hs,
            distribution_schedule=schedule(now),
            quotes=quotes,
            as_of=now,
        )
        self.assertEqual(row["status"], "STALE")


if __name__ == "__main__":
    unittest.main()
