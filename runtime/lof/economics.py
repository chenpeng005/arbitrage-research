from __future__ import annotations

from decimal import Decimal


def expected_p1_net_profit(
    *,
    subscription_amount: Decimal,
    expected_preserved_premium_rate: Decimal,
    subscription_fee_rate: Decimal = Decimal("0"),
    selling_fee_rate: Decimal = Decimal("0"),
    other_explicit_cost: Decimal = Decimal("0"),
    expected_nav_risk_cost: Decimal = Decimal("0"),
) -> Decimal:
    """Deterministic P1 economics helper.

    Rates are percentages: 5 means 5%, not 0.05.

    This function intentionally does not classify the result as an opportunity.
    Thresholds remain research-stage knowledge.
    """
    if subscription_amount < 0:
        raise ValueError("subscription_amount must be non-negative")

    hundred = Decimal("100")
    gross_spread = subscription_amount * expected_preserved_premium_rate / hundred
    subscription_fee = subscription_amount * subscription_fee_rate / hundred
    selling_fee = subscription_amount * selling_fee_rate / hundred

    return (
        gross_spread
        - subscription_fee
        - selling_fee
        - other_explicit_cost
        - expected_nav_risk_cost
    )
