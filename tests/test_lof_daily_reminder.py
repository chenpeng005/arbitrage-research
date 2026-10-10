from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from runtime.lof.daily_reminder import LofDailyReminderStore


def _row(
    code: str,
    *,
    status: str = "OPEN",
    limit_type: str = "NUMERIC",
    limit_amount: float | None = 1000,
    limit_raw: str | None = "1000",
    static_premium: float | None = 1.0,
    estimated_premium: float | None = 1.0,
    quote_status: str = "FRESH",
    official_status: str = "AVAILABLE",
    estimated_status: str = "AVAILABLE",
    state_time: str = "2026-10-09T14:50:00+08:00",
) -> dict:
    return {
        "code": code,
        "name": f"LOF-{code}",
        "exchange": "SSE",
        "price": 1.1,
        "quote_time": "2026-10-09T14:59:30+08:00",
        "quote_status": quote_status,
        "official_nav": 1.0,
        "official_nav_status": official_status,
        "official_nav_date": "2026-10-08",
        "official_nav_lag_label": "T-1",
        "static_premium_rate": static_premium,
        "estimated_nav": 1.02,
        "estimated_nav_status": estimated_status,
        "estimated_nav_time": "2026-10-09T14:59:30+08:00",
        "estimated_nav_quality": "HIGH",
        "estimated_nav_method": "INDEX_PROXY",
        "estimated_premium_rate": estimated_premium,
        "subscription_status": status,
        "daily_subscription_limit": limit_amount,
        "daily_subscription_limit_type": limit_type,
        "daily_subscription_limit_raw": limit_raw,
        "state_time": state_time,
        "state_source": "TEST",
    }


def _snapshot(
    snapshot_id: str,
    rows: list[dict],
    *,
    cutoff: str = "2026-10-09T15:00:00+08:00",
    fresh_count: int | None = None,
) -> dict:
    if fresh_count is None:
        fresh_count = sum(row.get("quote_status") == "FRESH" for row in rows)
    return {
        "contract_version": "LOF_MARKET_SNAPSHOT_V1",
        "snapshot_id": snapshot_id,
        "market_cutoff": cutoff,
        "universe_count": len(rows),
        "rows": rows,
        "quality_summary": {"quote_fresh_count": fresh_count},
    }


class LofDailyReminderTest(unittest.TestCase):
    def test_unknown_bridge_does_not_create_false_status_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LofDailyReminderStore(tmp)
            self.assertEqual(store.observe_snapshot(_snapshot("s1", [_row("501001")])), [])

            unknown = _row("501001", status="UNKNOWN", limit_type="UNKNOWN", limit_amount=None, limit_raw=None)
            self.assertEqual(store.observe_snapshot(_snapshot("s2", [unknown])), [])

            self.assertEqual(store.observe_snapshot(_snapshot("s3", [_row("501001")])), [])

            limited = _row("501001", status="LIMITED", state_time="2026-10-09T14:55:00+08:00")
            events = store.observe_snapshot(_snapshot("s4", [limited]))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "SUBSCRIPTION_STATUS_CHANGE")
            self.assertEqual(events[0]["old_status"], "OPEN")
            self.assertEqual(events[0]["new_status"], "LIMITED")

    def test_limit_changes_and_unlimited_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LofDailyReminderStore(tmp)
            store.observe_snapshot(_snapshot("s1", [_row("501001")]))

            changed = _row(
                "501001",
                limit_type="NUMERIC",
                limit_amount=10000,
                limit_raw="10000",
                state_time="2026-10-09T14:52:00+08:00",
            )
            events = store.observe_snapshot(_snapshot("s2", [changed]))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "SUBSCRIPTION_LIMIT_CHANGE")
            self.assertEqual(events[0]["old_limit_amount"], 1000.0)
            self.assertEqual(events[0]["new_limit_amount"], 10000.0)

            unlimited = _row(
                "501001",
                limit_type="UNLIMITED",
                limit_amount=None,
                limit_raw="不限额",
                state_time="2026-10-09T14:54:00+08:00",
            )
            events = store.observe_snapshot(_snapshot("s3", [unlimited]))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["old_limit_type"], "NUMERIC")
            self.assertEqual(events[0]["new_limit_type"], "UNLIMITED")

    def test_suspension_suppresses_mechanical_limit_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LofDailyReminderStore(tmp)
            store.observe_snapshot(_snapshot("s1", [_row("501001")]))

            suspended = _row(
                "501001",
                status="SUSPENDED",
                limit_type="NOT_APPLICABLE",
                limit_amount=None,
                limit_raw="1000",
                state_time="2026-10-09T14:55:00+08:00",
            )
            events = store.observe_snapshot(_snapshot("s2", [suspended]))
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0]["event_type"], "SUBSCRIPTION_STATUS_CHANGE")

            reminder = store.build_daily_reminder("2026-10-09")
            self.assertEqual(len(reminder["A_subscription_status_changes"]), 1)
            self.assertEqual(reminder["B_subscription_limit_changes"], [])

    def test_restart_does_not_duplicate_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = LofDailyReminderStore(tmp)
            first.observe_snapshot(_snapshot("s1", [_row("501001")]))
            limited = _row("501001", status="LIMITED", state_time="2026-10-09T14:55:00+08:00")
            events = first.observe_snapshot(_snapshot("s2", [limited]))
            self.assertEqual(len(events), 1)

            restarted = LofDailyReminderStore(tmp)
            events = restarted.observe_snapshot(_snapshot("s3", [limited]))
            self.assertEqual(events, [])
            reminder = restarted.build_daily_reminder("2026-10-09")
            self.assertEqual(len(reminder["A_subscription_status_changes"]), 1)

    def test_official_and_estimated_rankings_use_latest_reliable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LofDailyReminderStore(tmp)
            rows = []
            official_values = [8, 6, 4, 2, 1, -1, -2, -3, -4, -5, -6]
            estimated_values = [7, 5, 3, 2, 1, -0.5, -1.5, -2.5, -3.5, -4.5, -5.5]
            for idx, (official, estimated) in enumerate(zip(official_values, estimated_values), start=1):
                rows.append(_row(f"50{idx:04d}", static_premium=official, estimated_premium=estimated))

            store.observe_snapshot(_snapshot("early", rows, cutoff="2026-10-09T14:30:00+08:00"))

            later = [dict(row) for row in rows]
            later[0]["static_premium_rate"] = 9.0
            later[0]["estimated_premium_rate"] = 8.0
            later[1]["quote_status"] = "STALE"
            later[2]["estimated_nav_status"] = "UNAVAILABLE"
            store.observe_snapshot(_snapshot("close", later, cutoff="2026-10-09T15:00:00+08:00"))

            reminder = store.build_daily_reminder("2026-10-09")
            self.assertEqual(reminder["snapshot_id"], "close")
            self.assertEqual([r["static_premium_rate"] for r in reminder["C_official_premium_top5"]], [9.0, 4.0, 2.0, 1.0])
            self.assertEqual([r["static_premium_rate"] for r in reminder["C_official_discount_top5"]], [-6.0, -5.0, -4.0, -3.0, -2.0])
            self.assertEqual([r["estimated_premium_rate"] for r in reminder["D_estimated_premium_top5"]], [8.0, 2.0, 1.0])
            self.assertEqual([r["estimated_premium_rate"] for r in reminder["D_estimated_discount_top5"]], [-5.5, -4.5, -3.5, -2.5, -1.5])
            self.assertEqual(reminder["C_official_premium_top5"][0]["official_nav_lag_label"], "T-1")

    def test_zero_fresh_snapshot_does_not_replace_last_reliable_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LofDailyReminderStore(tmp)
            reliable = _snapshot("good", [_row("501001", static_premium=3.0)])
            store.observe_snapshot(reliable)

            stale = _row("501001", static_premium=99.0, quote_status="STALE")
            store.observe_snapshot(_snapshot("after-close", [stale], cutoff="2026-10-09T15:10:00+08:00", fresh_count=0))

            reminder = store.build_daily_reminder("2026-10-09")
            self.assertEqual(reminder["snapshot_id"], "good")
            self.assertEqual(reminder["C_official_premium_top5"][0]["static_premium_rate"], 3.0)

    def test_daily_output_is_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = LofDailyReminderStore(tmp)
            store.observe_snapshot(_snapshot("s1", [_row("501001")]))
            store.build_daily_reminder("2026-10-09")
            path = Path(tmp) / "daily_reminder" / "daily" / "2026-10-09.json"
            self.assertTrue(path.exists())
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["contract_version"], "LOF_DAILY_REMINDER_V0_1")


if __name__ == "__main__":
    unittest.main()
