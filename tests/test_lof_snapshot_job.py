from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.context_builder import EstimatedNavContextBuildResult
from runtime.lof.estimated_nav_lane import EstimatedNavContext
from runtime.lof.runtime_session import LofRuntimeSession
from runtime.lof.snapshot_job import run_snapshot_once


TZ = ZoneInfo("Asia/Shanghai")


class LofSnapshotJobTest(unittest.TestCase):
    @patch("runtime.lof.snapshot_job.LofRuntimeSession.build")
    def test_run_snapshot_once_persists_latest_snapshot(
        self,
        build_mock,
    ) -> None:
        now = datetime(2026, 9, 29, 13, 45, tzinfo=TZ)
        context = EstimatedNavContext(
            resolver_classes={},
            mapping_candidates={},
            r1_proxy_mappings={},
            qdii_proxy_registry={},
            previous_trading_day=date(2026, 9, 28),
        )
        build_result = EstimatedNavContextBuildResult(
            context=context,
            tracking_index_map={},
            r1_resolved_count=10,
            r1_unresolved_count=1,
        )
        session = LofRuntimeSession(
            universe=[],
            type_records={},
            estimated_nav_context=context,
            context_build=build_result,
            context_built_at=now,
        )
        session.collect = unittest.mock.Mock(
            return_value={
                "snapshot_id": "test-once",
                "collector_status": "PASS",
                "universe_count": 0,
                "quality_summary": {
                    "estimated_nav_available_count": 0,
                },
                "rows": [],
            }
        )
        build_mock.return_value = session

        with tempfile.TemporaryDirectory() as tmp:
            snapshot, path, returned_session = run_snapshot_once(
                data_root=tmp,
                as_of=now,
                snapshot_id="test-once",
                sse_universe_fixture_path="sse.json",
                szse_universe_fixture_path="szse.json",
                tracking_index_fixture_path=None,
            )
            self.assertEqual(snapshot["snapshot_id"], "test-once")
            self.assertTrue(path.exists())
            self.assertTrue(
                (Path(tmp) / "latest_market_snapshot.json").exists()
            )
            self.assertIs(returned_session, session)
            build_mock.assert_called_once_with(
                as_of=now,
                timeout=15,
                sse_universe_fixture_path="sse.json",
                szse_universe_fixture_path="szse.json",
                tracking_index_fixture_path=None,
            )


if __name__ == "__main__":
    unittest.main()
