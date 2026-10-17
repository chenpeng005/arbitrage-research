from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from runtime.lof.wechat_snapshot import (
    CONTRACT_VERSION,
    LofWechatSnapshotStore,
    build_lof_wechat_snapshot,
)


TZ = ZoneInfo("Asia/Shanghai")


def _row(
    code: str,
    *,
    premium: float,
    status: str = "OPEN",
    amount: float = 10000.0,
    quote_time: str = "2026-10-16T15:00:00+08:00",
    limit_type: str = "NUMERIC",
    limit_amount: float | None = 100000.0,
) -> dict:
    return {
        "code": code,
        "name": f"LOF-{code}",
        "exchange": "SSE",
        "price": 1.2,
        "quote_time": quote_time,
        "amount": amount,
        "official_nav": 1.0,
        "official_nav_status": "AVAILABLE",
        "official_nav_date": "2026-10-15",
        "official_nav_lag_label": "T-1",
        "static_premium_rate": premium,
        "subscription_status": status,
        "daily_subscription_limit": limit_amount,
        "daily_subscription_limit_type": limit_type,
        "daily_subscription_limit_raw": (
            None if limit_amount is None else str(limit_amount)
        ),
    }


def _snapshot(rows: list[dict], *, snapshot_id: str = "runtime-20261016T150000") -> dict:
    return {
        "contract_version": "LOF_MARKET_SNAPSHOT_V1",
        "snapshot_id": snapshot_id,
        "generated_at": "2026-10-16T15:00:01+08:00",
        "market_cutoff": "2026-10-16T15:00:01+08:00",
        "universe_count": len(rows),
        "rows": rows,
        "quality_summary": {"row_count": len(rows)},
    }


class LofWechatSnapshotTest(unittest.TestCase):
    def test_filters_suspended_zero_turnover_unknown_limit_and_old_quote(self) -> None:
        rows = [
            _row("501001", premium=30.0, status="SUSPENDED"),
            _row("501002", premium=25.0, amount=0.0),
            _row(
                "501003",
                premium=20.0,
                limit_type="UNKNOWN",
                limit_amount=None,
            ),
            _row(
                "501004",
                premium=18.0,
                quote_time="2026-10-15T15:00:00+08:00",
            ),
            _row("501005", premium=10.0),
            _row("501006", premium=9.0),
            _row("501007", premium=8.0),
            _row("501008", premium=7.0),
            _row(
                "501009",
                premium=6.0,
                status="LIMITED",
                limit_amount=2000.0,
            ),
        ]
        payload = build_lof_wechat_snapshot(_snapshot(rows))

        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)
        self.assertEqual(payload["source_owner"], "LOF_RUNTIME")
        self.assertEqual(payload["logical_date"], "2026-10-16")
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(
            [row["code"] for row in payload["rows"]],
            ["501005", "501006", "501007", "501008", "501009"],
        )
        self.assertTrue(all(row["turnover_cny"] > 0 for row in payload["rows"]))
        self.assertTrue(
            all(
                row["subscription_status"] in {"OPEN", "LIMITED"}
                for row in payload["rows"]
            )
        )

    def test_unlimited_limit_is_eligible_without_numeric_amount(self) -> None:
        rows = [
            _row(
                f"50100{idx}",
                premium=10.0 - idx,
                limit_type="UNLIMITED" if idx == 1 else "NUMERIC",
                limit_amount=None if idx == 1 else 1000.0,
            )
            for idx in range(1, 6)
        ]
        payload = build_lof_wechat_snapshot(_snapshot(rows))
        self.assertEqual(payload["status"], "READY")
        unlimited = next(row for row in payload["rows"] if row["code"] == "501001")
        self.assertEqual(unlimited["subscription_limit_type"], "UNLIMITED")
        self.assertIsNone(unlimited["subscription_limit_cny"])
        self.assertEqual(unlimited["eligibility_blockers"], [])

    def test_insufficient_rows_fail_closed(self) -> None:
        payload = build_lof_wechat_snapshot(
            _snapshot(
                [
                    _row("501001", premium=3.0),
                    _row("501002", premium=2.0),
                ]
            )
        )
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(
            payload["block_reason"],
            "INSUFFICIENT_ELIGIBLE_ROWS",
        )
        self.assertEqual(len(payload["rows"]), 2)

    def test_persistence_archives_cutoff_safe_snapshots(self) -> None:
        rows = [
            _row(f"50100{idx}", premium=10.0 - idx)
            for idx in range(1, 7)
        ]
        with tempfile.TemporaryDirectory() as tmp:
            store = LofWechatSnapshotStore(tmp)
            first_source = _snapshot(rows, snapshot_id="runtime-20261016T150000")
            first_payload, first_path = store.build_and_persist(
                first_source,
                generated_at=datetime(2026, 10, 16, 15, 1, tzinfo=TZ),
            )
            second_source = _snapshot(rows, snapshot_id="runtime-20261016T152000")
            second_payload, second_path = store.build_and_persist(
                second_source,
                generated_at=datetime(2026, 10, 16, 15, 21, tzinfo=TZ),
            )

            latest = Path(tmp) / "wechat_snapshot" / "latest.json"
            daily = Path(tmp) / "wechat_snapshot" / "daily" / "2026-10-16.json"
            self.assertTrue(first_path.exists())
            self.assertTrue(second_path.exists())
            self.assertTrue(latest.exists())
            self.assertTrue(daily.exists())
            self.assertNotEqual(first_path, second_path)

            loaded = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(
                loaded["source_snapshot_id"], second_source["snapshot_id"]
            )
            self.assertEqual(loaded["rows"], second_payload["rows"])
            self.assertLess(latest.stat().st_size, 100_000)

            asof = store.load_latest_at_or_before(
                datetime(2026, 10, 16, 15, 10, tzinfo=TZ)
            )
            self.assertIsNotNone(asof)
            self.assertEqual(
                asof["source_snapshot_id"],
                first_payload["source_snapshot_id"],
            )


if __name__ == "__main__":
    unittest.main()
