from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.classification import FundTypeRecord
from runtime.lof.context_builder import EstimatedNavContextBuildResult
from runtime.lof.estimated_nav_lane import EstimatedNavContext
from runtime.lof.runtime_session import LofRuntimeSession
from runtime.lof.universe import LofIdentity


TZ = ZoneInfo("Asia/Shanghai")


class LofRuntimeSessionTest(unittest.TestCase):
    @patch("runtime.lof.runtime_session.build_estimated_nav_context")
    @patch("runtime.lof.runtime_session.fetch_previous_trading_day")
    @patch("runtime.lof.runtime_session.fetch_fund_type_map")
    @patch("runtime.lof.runtime_session.fetch_all_lof_universe")
    def test_build_prepares_slow_lane_once(
        self,
        universe_mock,
        type_map_mock,
        previous_day_mock,
        context_mock,
    ) -> None:
        now = datetime(2026, 9, 29, 13, 30, tzinfo=TZ)
        universe = [
            LofIdentity(
                code="163407",
                name="兴全沪深300指数(LOF)A",
                exchange="SZSE",
            )
        ]
        universe_mock.return_value = universe
        type_map_mock.return_value = {
            "163407": FundTypeRecord(
                code="163407",
                name_raw="兴全沪深300指数(LOF)A",
                fund_type_raw="指数型-股票",
                lof_type="EQUITY",
                source="TEST",
            )
        }
        previous_day_mock.return_value = date(2026, 9, 28)
        empty_context = EstimatedNavContext(
            resolver_classes={},
            mapping_candidates={},
            r1_proxy_mappings={},
            qdii_proxy_registry={},
            previous_trading_day=date(2026, 9, 28),
        )
        context_mock.return_value = EstimatedNavContextBuildResult(
            context=empty_context,
            tracking_index_map={},
            r1_resolved_count=1,
            r1_unresolved_count=0,
        )

        session = LofRuntimeSession.build(
            as_of=now,
            sse_universe_fixture_path="sse.json",
            szse_universe_fixture_path="szse.json",
        )

        self.assertEqual(len(session.universe), 1)
        self.assertEqual(
            session.estimated_nav_context.previous_trading_day,
            date(2026, 9, 28),
        )
        self.assertEqual(session.context_build.r1_resolved_count, 1)
        universe_mock.assert_called_once_with(
            timeout=15,
            sse_fixture_path="sse.json",
            szse_fixture_path="szse.json",
            szse_relay_base_url=unittest.mock.ANY,
            as_of=now,
            szse_relay_max_age_seconds=unittest.mock.ANY,
        )
        previous_day_mock.assert_called_once()

    @patch("runtime.lof.runtime_session.collect_market_snapshot")
    def test_collect_reuses_cached_universe_and_type_records(
        self,
        collect_mock,
    ) -> None:
        now = datetime(2026, 9, 29, 13, 30, tzinfo=TZ)
        universe = [
            LofIdentity(code="163407", name="示例", exchange="SZSE")
        ]
        types = {
            ("SZSE", "163407"): FundTypeRecord(
                code="163407",
                name_raw="示例",
                fund_type_raw="指数型-股票",
                lof_type="EQUITY",
                source="TEST",
            )
        }
        context = EstimatedNavContext(
            resolver_classes={},
            mapping_candidates={},
            r1_proxy_mappings={},
            qdii_proxy_registry={},
            previous_trading_day=date(2026, 9, 28),
        )
        build = EstimatedNavContextBuildResult(
            context=context,
            tracking_index_map={},
            r1_resolved_count=0,
            r1_unresolved_count=0,
        )
        session = LofRuntimeSession(
            universe=universe,
            type_records=types,
            estimated_nav_context=context,
            context_build=build,
            context_built_at=now,
        )
        collect_mock.return_value = {"snapshot_id": "x"}

        session.collect(
            generated_at=now,
            market_cutoff=now,
        )

        kwargs = collect_mock.call_args.kwargs
        self.assertIs(kwargs["universe_override"], universe)
        self.assertIs(kwargs["type_records_override"], types)
        self.assertIs(kwargs["estimated_nav_context"], context)


if __name__ == "__main__":
    unittest.main()
