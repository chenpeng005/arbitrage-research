from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from runtime.lof.runtime_loop import (
    is_market_refresh_window,
    run_runtime_loop,
)
from runtime.lof.universe import LofIdentity


TZ = ZoneInfo("Asia/Shanghai")


class _ContextBuild:
    r1_resolved_count = 1
    r1_unresolved_count = 0


class _FakeSession:
    def __init__(self) -> None:
        self.universe = [
            LofIdentity(
                code="501001",
                name="TEST",
                exchange="SSE",
            )
        ]
        self.context_build = _ContextBuild()
        self.szse_transport = "DIRECT_OFFICIAL"
        self.szse_relay_bundle = None
        self.collect_calls = []

    def collect(self, **kwargs):
        self.collect_calls.append(kwargs)
        return {
            "snapshot_id": kwargs.get("snapshot_id"),
            "collector_status": "PASS",
            "universe_count": 1,
            "rows": [{"code": "501001"}],
            "quality_summary": {
                "quote_fresh_count": 1,
                "estimated_nav_available_count": 1,
            },
            "lane_errors": {},
        }


class LofRuntimeLoopTest(unittest.TestCase):
    def test_market_refresh_window(self) -> None:
        self.assertTrue(
            is_market_refresh_window(
                datetime(2026, 9, 29, 10, 0, tzinfo=TZ)
            )
        )
        self.assertTrue(
            is_market_refresh_window(
                datetime(2026, 9, 29, 14, 30, tzinfo=TZ)
            )
        )
        self.assertFalse(
            is_market_refresh_window(
                datetime(2026, 9, 29, 12, 0, tzinfo=TZ)
            )
        )
        self.assertFalse(
            is_market_refresh_window(
                datetime(2026, 10, 3, 10, 0, tzinfo=TZ)
            )
        )

    @patch("runtime.lof.runtime_loop.LofSnapshotStore")
    @patch("runtime.lof.runtime_loop.fetch_all_trade_states")
    @patch("runtime.lof.runtime_loop.fetch_all_official_nav")
    @patch("runtime.lof.runtime_loop.LofRuntimeSession.build")
    def test_slow_lanes_are_cached_between_fast_cycles(
        self,
        build_mock,
        nav_mock,
        state_mock,
        store_cls,
    ) -> None:
        session = _FakeSession()
        build_mock.return_value = session
        nav_cache = [Mock()]
        state_cache = [Mock()]
        nav_mock.return_value = nav_cache
        state_mock.return_value = state_cache
        store_cls.return_value.persist.return_value = "snapshot.json"

        with tempfile.TemporaryDirectory() as tmp:
            result = run_runtime_loop(
                data_root=tmp,
                quote_interval_seconds=0,
                off_hours_interval_seconds=0,
                state_refresh_seconds=3600,
                nav_refresh_seconds=3600,
                session_refresh_seconds=3600,
                timeout=1,
                max_cycles=2,
                sleep_fn=lambda seconds: None,
                now_fn=lambda: datetime(
                    2026, 9, 29, 10, 0, tzinfo=TZ
                ),
            )

        self.assertEqual(result, 0)
        self.assertEqual(build_mock.call_count, 1)
        self.assertEqual(nav_mock.call_count, 1)
        self.assertEqual(state_mock.call_count, 1)
        self.assertEqual(len(session.collect_calls), 2)
        for call in session.collect_calls:
            self.assertIs(call["official_nav_override"], nav_cache)
            self.assertIs(call["trade_states_override"], state_cache)


if __name__ == "__main__":
    unittest.main()
