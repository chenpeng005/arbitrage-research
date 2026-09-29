from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal

from runtime.lof.economics import expected_p1_net_profit
from runtime.lof.models import LofMarketState
from runtime.lof.premium import premium_projection, premium_rate


class LofRuntimeBaselineTest(unittest.TestCase):
    def test_premium_rate(self) -> None:
        self.assertEqual(
            premium_rate(Decimal("1.10"), Decimal("1.00")),
            Decimal("10.0"),
        )
        self.assertEqual(
            premium_rate(Decimal("0.95"), Decimal("1.00")),
            Decimal("-5.00"),
        )

    def test_invalid_nav_does_not_produce_fake_premium(self) -> None:
        self.assertIsNone(premium_rate(Decimal("1.00"), None))
        self.assertIsNone(premium_rate(Decimal("1.00"), Decimal("0")))

    def test_static_and_estimated_premium_are_separate(self) -> None:
        state = LofMarketState(
            code="161128",
            name="示例QDII-LOF",
            exchange="SZSE",
            price=Decimal("1.10"),
            price_time=datetime(2026, 9, 29, 10, 30),
            official_nav=Decimal("1.00"),
            official_nav_date=date(2026, 9, 28),
            estimated_nav=Decimal("1.05"),
            estimated_nav_time=datetime(2026, 9, 29, 10, 30),
            nav_quality="ESTIMATED",
        )
        projected = premium_projection(state)
        self.assertEqual(projected["static_premium_rate"], Decimal("10.0"))
        self.assertEqual(
            projected["estimated_premium_rate"],
            Decimal("4.761904761904761904761904800"),
        )

    def test_p1_economics_is_calculation_not_gate(self) -> None:
        profit = expected_p1_net_profit(
            subscription_amount=Decimal("100"),
            expected_preserved_premium_rate=Decimal("8"),
            subscription_fee_rate=Decimal("0.1"),
            selling_fee_rate=Decimal("0.05"),
            other_explicit_cost=Decimal("0.10"),
            expected_nav_risk_cost=Decimal("1.00"),
        )
        self.assertEqual(profit, Decimal("6.75"))

    def test_p1_rejects_negative_subscription_amount(self) -> None:
        with self.assertRaises(ValueError):
            expected_p1_net_profit(
                subscription_amount=Decimal("-1"),
                expected_preserved_premium_rate=Decimal("5"),
            )


if __name__ == "__main__":
    unittest.main()
