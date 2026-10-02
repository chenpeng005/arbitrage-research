import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from runtime.lof.p1_execution_precheck import (
    build_execution_precheck_snapshot,
    evaluate_execution_precheck,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _row(
    *,
    code="501312",
    exchange="SSE",
    status="LIMITED",
    limit=20.0,
    fund_min=10.0,
    sell_days=None,
    confirmation_days=2,
    limit_scope="UNKNOWN",
    lof_type="QDII_EQUITY",
    raw="QDII-普通股票",
    resolver="R4_QDII_OTHER",
):
    return {
        "code": code,
        "name": code,
        "exchange": exchange,
        "subscription_status": status,
        "daily_subscription_limit": limit,
        "minimum_subscription_amount": fund_min,
        "subscription_confirmation_days": confirmation_days,
        "subscription_to_sell_days": sell_days,
        "limit_scope": limit_scope,
        "lof_type": lof_type,
        "fund_type_raw": raw,
        "resolver_class": resolver,
    }


class P1ExecutionPrecheckTests(unittest.TestCase):
    def test_sse_small_limit_exposes_order_conflict(self):
        result = evaluate_execution_precheck(_row())
        self.assertEqual(result["exchange_min_order_amount"], 1000.0)
        self.assertEqual(result["effective_min_order_amount"], 1000.0)
        self.assertTrue(result["limit_vs_order_min_conflict"])
        self.assertTrue(result["requires_over_limit_partial_confirmation"])
        self.assertIn(
            "OVER_LIMIT_PARTIAL_CONFIRMATION_UNVERIFIED",
            result["blockers"],
        )
        self.assertIn(
            "BROKER_OVER_LIMIT_SUPPORT_UNVERIFIED",
            result["blockers"],
        )
        self.assertIn("SELLABLE_TIMING_UNKNOWN_QDII", result["blockers"])
        self.assertEqual(result["execution_precheck_state"], "INCOMPLETE")
        self.assertFalse(result["eligible_for_opportunity"])

    def test_sse_large_limit_has_no_order_conflict(self):
        result = evaluate_execution_precheck(
            _row(code="501300", limit=50_000_000.0, limit_scope="FUND_ACCOUNT")
        )
        self.assertFalse(result["limit_vs_order_min_conflict"])
        self.assertNotIn(
            "OVER_LIMIT_PARTIAL_CONFIRMATION_UNVERIFIED",
            result["blockers"],
        )
        self.assertIn("SELLABLE_TIMING_UNKNOWN_QDII", result["blockers"])

    def test_szse_uses_fund_minimum_not_sse_floor(self):
        result = evaluate_execution_precheck(
            _row(
                code="162411",
                exchange="SZSE",
                limit=10.0,
                fund_min=10.0,
                limit_scope="FUND_ACCOUNT",
                resolver="R3_QDII_INDEX",
                raw="指数型-海外股票",
            )
        )
        self.assertIsNone(result["exchange_min_order_amount"])
        self.assertEqual(result["exchange_order_increment"], 1.0)
        self.assertEqual(result["effective_min_order_amount"], 10.0)
        self.assertFalse(result["limit_vs_order_min_conflict"])

    def test_suspended_subscription_is_blocked(self):
        result = evaluate_execution_precheck(
            _row(status="SUSPENDED", limit=None)
        )
        self.assertEqual(result["execution_precheck_state"], "BLOCKED")
        self.assertEqual(result["blockers"], ["SUBSCRIPTION_SUSPENDED"])

    def test_complete_evidence_only_clears_precheck_not_opportunity(self):
        result = evaluate_execution_precheck(
            _row(
                code="501300",
                limit=50_000_000.0,
                sell_days=2,
                limit_scope="FUND_ACCOUNT",
            )
        )
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["execution_precheck_state"], "PRECHECK_CLEAR")
        self.assertFalse(result["eligible_for_opportunity"])

    def test_snapshot_only_includes_qdii_scope(self):
        qdii = _row(
            code="501300",
            limit=50_000_000.0,
            sell_days=2,
            limit_scope="FUND_ACCOUNT",
        )
        domestic = _row(
            code="160916",
            exchange="SZSE",
            status="OPEN",
            limit=None,
            fund_min=10.0,
            sell_days=2,
            confirmation_days=1,
            limit_scope="UNKNOWN",
            lof_type="MIXED",
            raw="混合型-偏股",
            resolver="R2_DOMESTIC_OTHER",
        )
        snapshot = build_execution_precheck_snapshot(
            main_snapshot={
                "snapshot_id": "main-1",
                "rows": [qdii, domestic],
            },
            generated_at=datetime(2026, 10, 9, 14, 0, tzinfo=SHANGHAI_TZ),
        )
        self.assertEqual(snapshot["summary"]["row_count"], 1)
        self.assertEqual(snapshot["rows"][0]["code"], "501300")
        self.assertEqual(snapshot["summary"]["precheck_clear_count"], 1)


if __name__ == "__main__":
    unittest.main()
