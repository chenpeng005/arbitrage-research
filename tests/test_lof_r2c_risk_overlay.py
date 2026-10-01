[Reading 275 lines from start (total: 275 lines, 0 remaining)]

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
from runtime.lof.r2c_risk_holdings import (
    RiskHoldingsSnapshot,
    RiskPosition,
    parse_bond_tables,
)
from runtime.lof.r2c_risk_overlay import (
    calculate_rows,
    load_registry,
)


TZ = ZoneInfo("Asia/Shanghai")


def profile():
    return {
        "name": "Test Fund",
        "band_group": "C4_HIGH",
        "driver": "EQUITY_CB",
        "quality_candidate": "MEDIUM",
        "backtest_mae_pct": 0.1,
        "backtest_p90_pct": 0.2,
    }


def market_row(lag: str = "T-1"):
    return {
        "code": "160641",
        "name": "Test Fund",
        "official_nav": 1.0,
        "official_nav_date": "2026-09-30",
        "official_nav_lag_label": lag,
        "price": 1.05,
    }


def holdings(now: datetime):
    positions = (
        RiskPosition(
            "STOCK",
            "sz000001",
            "000001",
            "Stock",
            Decimal("0.20"),
        ),
        RiskPosition(
            "CB",
            "sz123176",
            "123176",
            "Bond",
            Decimal("0.60"),
        ),
    )
    return RiskHoldingsSnapshot(
        fund_code="160641",
        as_of_date=date(2026, 6, 30),
        first_seen_at=now - timedelta(days=30),
        fetched_at=now,
        positions=positions,
        stock_weight=Decimal("0.20"),
        cb_weight=Decimal("0.60"),
        total_risk_weight=Decimal("0.80"),
        identity="identity",
    )


def schedule(now: datetime, cash: Decimal = Decimal("0")):
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
        fund_code="160641",
        fetched_at=now,
        events=events,
    )


class R2CRiskOverlayTest(unittest.TestCase):
    def test_registry_excludes_rejected_fund(self):
        rows = load_registry()
        self.assertEqual(len(rows), 16)
        self.assertNotIn("161626", rows)
        self.assertIn("164814", rows)

    def test_bond_parser_separates_report_periods(self):
        text = """
        截止至：<font>2026-06-30</font>
        <table><tbody>
          <tr><td>1</td><td>123176</td><td>精测转2</td><td>4.12%</td><td>1</td></tr>
          <tr><td>2</td><td>019750</td><td>24特国04</td><td>10.09%</td><td>1</td></tr>
        </tbody></table>
        截止至：<font>2026-03-31</font>
        <table><tbody>
          <tr><td>1</td><td>113042</td><td>上银转债</td><td>3.00%</td><td>1</td></tr>
        </tbody></table>
        """
        rows = parse_bond_tables(text)
        self.assertEqual(
            [x.symbol for x in rows[date(2026, 6, 30)]],
            ["sz123176"],
        )
        self.assertEqual(
            [x.symbol for x in rows[date(2026, 3, 31)]],
            ["sh113042"],
        )

    def test_stock_and_cb_overlay_calculation(self):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
        rows = calculate_rows(
            main_snapshot={"rows": [market_row()]},
            registry={"160641": profile()},
            holdings_by_fund={"160641": holdings(now)},
            distributions={"160641": schedule(now)},
            quotes={
                "sz000001": LiveQuote(
                    "sz000001",
                    Decimal("110"),
                    Decimal("100"),
                    now,
                ),
                "sz123176": LiveQuote(
                    "sz123176",
                    Decimal("95"),
                    Decimal("100"),
                    now,
                ),
            },
            as_of=now,
        )
        row = rows[0]
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertAlmostEqual(
            row["estimated_return"],
            -0.01,
            places=8,
        )
        self.assertAlmostEqual(
            row["shadow_estimated_nav"],
            0.99,
            places=8,
        )

    def test_cash_distribution_is_subtracted(self):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
        rows = calculate_rows(
            main_snapshot={"rows": [market_row()]},
            registry={"160641": profile()},
            holdings_by_fund={"160641": holdings(now)},
            distributions={
                "160641": schedule(
                    now,
                    Decimal("0.20"),
                )
            },
            quotes={
                "sz000001": LiveQuote(
                    "sz000001",
                    Decimal("100"),
                    Decimal("100"),
                    now,
                ),
                "sz123176": LiveQuote(
                    "sz123176",
                    Decimal("100"),
                    Decimal("100"),
                    now,
                ),
            },
            as_of=now,
        )
        self.assertAlmostEqual(
            rows[0]["shadow_estimated_nav"],
            0.80,
            places=8,
        )

    def test_t2_nav_fails_closed(self):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
        rows = calculate_rows(
            main_snapshot={
                "rows": [market_row("T-2")]
            },
            registry={"160641": profile()},
            holdings_by_fund={"160641": holdings(now)},
            distributions={"160641": schedule(now)},
            quotes={},
            as_of=now,
        )
        self.assertEqual(
            rows[0]["status"],
            "UNAVAILABLE",
        )
        self.assertEqual(
            rows[0]["error"],
            "OFFICIAL_NAV_NOT_T1",
        )

    def test_low_live_coverage_fails_closed(self):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
        rows = calculate_rows(
            main_snapshot={"rows": [market_row()]},
            registry={"160641": profile()},
            holdings_by_fund={"160641": holdings(now)},
            distributions={"160641": schedule(now)},
            quotes={
                "sz000001": LiveQuote(
                    "sz000001",
                    Decimal("101"),
                    Decimal("100"),
                    now,
                )
            },
            as_of=now,
        )
        self.assertEqual(
            rows[0]["status"],
            "UNAVAILABLE",
        )
        self.assertIn(
            "LIVE_RISK_WEIGHT_COVERAGE_TOO_LOW",
            rows[0]["error"],
        )

    def test_old_quotes_mark_stale(self):
        now = datetime(2026, 10, 8, 10, 0, tzinfo=TZ)
        old = now - timedelta(minutes=10)
        rows = calculate_rows(
            main_snapshot={"rows": [market_row()]},
            registry={"160641": profile()},
            holdings_by_fund={"160641": holdings(now)},
            distributions={"160641": schedule(now)},
            quotes={
                "sz000001": LiveQuote(
                    "sz000001",
                    Decimal("101"),
                    Decimal("100"),
                    old,
                ),
                "sz123176": LiveQuote(
                    "sz123176",
                    Decimal("101"),
                    Decimal("100"),
                    old,
                ),
            },
            as_of=now,
        )
        self.assertEqual(
            rows[0]["status"],
            "STALE",
        )


if __name__ == "__main__":
    unittest.main()
