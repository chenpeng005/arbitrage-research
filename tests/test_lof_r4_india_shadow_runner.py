import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.india_index_quote import DelayedGlobalIndexQuote
from runtime.lof.r4_india_shadow_runner import (
    calculate_shadow_row,
    collect_once,
)
from runtime.lof.snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _main_snapshot(
    *,
    quote_status: str = "FRESH",
) -> dict:
    return {
        "snapshot_id": "main-test-1",
        "rows": [
            {
                "code": "164824",
                "name": "印度基金LOF",
                "price": 1.30,
                "quote_status": quote_status,
                "quote_time": "2026-09-30T14:50:00+08:00",
                "official_nav": 1.20,
                "official_nav_date": "2026-09-29",
            }
        ],
    }


def _quote(
    *,
    quote_time: datetime,
    current: str = "101",
    previous_close: str = "100",
) -> DelayedGlobalIndexQuote:
    return DelayedGlobalIndexQuote(
        secid="SENSEX.OTC",
        code="SENSEX",
        name="印度孟买SENSEX指数",
        current=Decimal(current),
        previous_close=Decimal(previous_close),
        quote_time=quote_time,
        source="WSCN_MARKET_REAL",
        error=None,
    )


class R4IndiaShadowRunnerTests(unittest.TestCase):
    def test_available_shadow_persists_separate_snapshot(self):
        now = datetime(2026, 9, 30, 14, 50, tzinfo=SHANGHAI_TZ)
        quote = _quote(
            quote_time=datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ)
        )
        with tempfile.TemporaryDirectory() as main_root, tempfile.TemporaryDirectory() as state_root:
            LofSnapshotStore(main_root).persist(_main_snapshot())
            with patch(
                "runtime.lof.r4_india_shadow_runner.fetch_india_sensex_quote",
                return_value=quote,
            ):
                snapshot = collect_once(
                    main_data_root=main_root,
                    state_root=state_root,
                    now=now,
                )

            row = snapshot["rows"][0]
            self.assertEqual(row["status"], "AVAILABLE")
            self.assertEqual(row["quality"], "LOW")
            self.assertAlmostEqual(row["shadow_estimated_nav"], 1.212, places=9)
            self.assertIsNotNone(row["shadow_premium_rate"])
            self.assertFalse(row["eligible_for_main"])
            latest = Path(state_root) / "r4_india_shadow.json"
            self.assertTrue(latest.exists())
            persisted = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["source_market_snapshot_id"],
                "main-test-1",
            )

    def test_stale_proxy_never_outputs_shadow_premium(self):
        now = datetime(2026, 10, 2, 14, 0, tzinfo=SHANGHAI_TZ)
        quote = _quote(
            quote_time=datetime(2026, 10, 1, 17, 58, 42, tzinfo=SHANGHAI_TZ),
            current="71909.7",
            previous_close="72480.29",
        )
        row = calculate_shadow_row(
            main_snapshot=_main_snapshot(),
            quote=quote,
            as_of=now,
        )
        self.assertEqual(row["status"], "STALE")
        self.assertIsNone(row["shadow_estimated_nav"])
        self.assertIsNone(row["shadow_premium_rate"])
        self.assertFalse(row["eligible_for_main"])

    def test_stale_lof_market_quote_blocks_shadow_premium(self):
        now = datetime(2026, 9, 30, 14, 50, tzinfo=SHANGHAI_TZ)
        quote = _quote(
            quote_time=datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ)
        )
        row = calculate_shadow_row(
            main_snapshot=_main_snapshot(quote_status="STALE"),
            quote=quote,
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertIsNotNone(row["shadow_estimated_nav"])
        self.assertIsNone(row["shadow_premium_rate"])

    def test_missing_fund_in_main_snapshot_fails_closed(self):
        now = datetime(2026, 9, 30, 14, 50, tzinfo=SHANGHAI_TZ)
        quote = _quote(
            quote_time=datetime(2026, 9, 30, 14, 40, tzinfo=SHANGHAI_TZ)
        )
        row = calculate_shadow_row(
            main_snapshot={"snapshot_id": "x", "rows": []},
            quote=quote,
            as_of=now,
        )
        self.assertEqual(row["status"], "UNAVAILABLE")
        self.assertEqual(row["error"], "FUND_NOT_IN_MAIN_SNAPSHOT")
        self.assertFalse(row["eligible_for_main"])


if __name__ == "__main__":
    unittest.main()
