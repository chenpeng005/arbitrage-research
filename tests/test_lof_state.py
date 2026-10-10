from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from runtime.lof.state import (
    normalize_redemption_status,
    normalize_subscription_limit,
    normalize_subscription_status,
    parse_eastmoney_trade_state,
)


class LofTradeStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 9, 29, 4, 0, tzinfo=timezone.utc)

    def test_parse_limited_subscription(self) -> None:
        payload = {
            "Datas": {
                "SGZT": "限大额",
                "SHZT": "开放赎回",
                "MAXSG": "1000",
                "MINSG": "10",
                "SSBCFMDATA": "2",
                "sg": [
                    {
                        "money": "购买金额 < 100万",
                        "source": "1.20%",
                        "rate": "0.12%",
                    }
                ],
                "sh": [
                    {
                        "time": "持有期限 < 7天",
                        "rate": "1.50%",
                    }
                ],
            }
        }
        row = parse_eastmoney_trade_state(
            payload,
            code="161128",
            fetched_at=self.now,
        )
        self.assertEqual(row.subscription_status, "LIMITED")
        self.assertEqual(row.redemption_status, "OPEN")
        self.assertEqual(row.daily_subscription_limit, Decimal("1000"))
        self.assertEqual(row.minimum_subscription_amount, Decimal("10"))
        self.assertEqual(row.subscription_confirmation_days, 2)

        # Critical semantic guardrails.
        self.assertEqual(row.limit_scope, "UNKNOWN")
        self.assertIsNone(row.subscription_to_sell_days)
        self.assertEqual(row.fee_source, "EASTMONEY_CHANNEL_REFERENCE")

    def test_paused_subscription_can_still_report_a_limit(self) -> None:
        payload = {
            "Datas": {
                "SGZT": "暂停申购",
                "SHZT": "开放赎回",
                "MAXSG": "100",
                "SSBCFMDATA": "2",
            }
        }
        row = parse_eastmoney_trade_state(
            payload,
            code="501225",
            fetched_at=self.now,
        )
        self.assertEqual(row.subscription_status, "SUSPENDED")
        self.assertEqual(row.daily_subscription_limit, Decimal("100"))
        # P1 must inspect status before treating MAXSG as executable capacity.
        self.assertIsNone(row.subscription_to_sell_days)

    def test_missing_state_is_explicit(self) -> None:
        row = parse_eastmoney_trade_state(
            {},
            code="000000",
            fetched_at=self.now,
        )
        self.assertEqual(row.subscription_status, "UNKNOWN")
        self.assertEqual(row.redemption_status, "UNKNOWN")
        self.assertEqual(row.error, "NO_STATE_DATA")

    def test_subscription_limit_semantics_are_preserved(self) -> None:
        limit_type, amount, raw = normalize_subscription_limit("不限额")
        self.assertEqual(limit_type, "UNLIMITED")
        self.assertIsNone(amount)
        self.assertEqual(raw, "不限额")

        limit_type, amount, raw = normalize_subscription_limit("999999999")
        self.assertEqual(limit_type, "NUMERIC")
        self.assertEqual(amount, Decimal("999999999"))
        self.assertEqual(raw, "999999999")

        limit_type, amount, raw = normalize_subscription_limit("not-a-number")
        self.assertEqual(limit_type, "UNKNOWN")
        self.assertIsNone(amount)
        self.assertEqual(raw, "not-a-number")

        payload = {
            "Datas": {
                "SGZT": "开放申购",
                "SHZT": "开放赎回",
                "MAXSG": "不限额",
            }
        }
        row = parse_eastmoney_trade_state(
            payload, code="501001", fetched_at=self.now
        )
        self.assertEqual(row.daily_subscription_limit_type, "UNLIMITED")
        self.assertIsNone(row.daily_subscription_limit)
        self.assertEqual(row.daily_subscription_limit_raw, "不限额")

    def test_normalizers_are_conservative(self) -> None:
        self.assertEqual(normalize_subscription_status("开放申购"), "OPEN")
        self.assertEqual(normalize_subscription_status("限大额"), "LIMITED")
        self.assertEqual(
            normalize_subscription_status("暂停大额申购"),
            "LIMITED",
        )
        self.assertEqual(normalize_subscription_status("暂停申购"), "SUSPENDED")
        self.assertEqual(normalize_subscription_status("其他"), "UNKNOWN")

        self.assertEqual(normalize_redemption_status("开放赎回"), "OPEN")
        self.assertEqual(normalize_redemption_status("暂停赎回"), "SUSPENDED")
        self.assertEqual(normalize_redemption_status("其他"), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
