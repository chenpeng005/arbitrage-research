from __future__ import annotations

import unittest
from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runtime.lof.nav import OfficialNavRecord
from runtime.lof.quote import QuoteRecord
from runtime.lof.snapshot import (
    CONTRACT_VERSION,
    build_market_snapshot,
    validate_market_snapshot,
)
from runtime.lof.universe import LofIdentity


TZ = ZoneInfo("Asia/Shanghai")


class LofMarketSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cutoff = datetime(2026, 9, 29, 11, 30, 0, tzinfo=TZ)
        self.universe = [
            LofIdentity(
                code="501001",
                name="财通精选混合LOF",
                exchange="SSE",
                source="SSE_OFFICIAL",
            ),
            LofIdentity(
                code="161128",
                name="标普信息科技LOF",
                exchange="SZSE",
                source="SZSE_OFFICIAL",
            ),
        ]

    def test_builder_preserves_all_universe_rows(self) -> None:
        quotes = [
            QuoteRecord(
                code="501001",
                exchange="SSE",
                name="财通精选混合LOF",
                price=Decimal("1.429"),
                quote_time=datetime(2026, 9, 29, 11, 29, 50, tzinfo=TZ),
                pct_change=Decimal("-1.31"),
                volume=Decimal("100"),
                amount=Decimal("90000"),
                source="TENCENT_QUOTE",
            )
        ]
        navs = [
            OfficialNavRecord(
                code="501001",
                exchange="SSE",
                nav=Decimal("1.445"),
                nav_date=date(2026, 9, 28),
                fetched_at=self.cutoff,
                source="SSE_OFFICIAL",
            )
        ]

        snapshot = build_market_snapshot(
            universe=self.universe,
            quotes=quotes,
            official_navs=navs,
            generated_at=self.cutoff,
            market_cutoff=self.cutoff,
            max_quote_age_seconds=30,
            snapshot_id="test-snapshot",
        )

        self.assertEqual(snapshot["contract_version"], CONTRACT_VERSION)
        self.assertEqual(snapshot["universe_count"], 2)
        self.assertEqual(len(snapshot["rows"]), 2)

        by_code = {row["code"]: row for row in snapshot["rows"]}
        self.assertEqual(by_code["501001"]["quote_status"], "FRESH")
        self.assertEqual(by_code["501001"]["official_nav_status"], "AVAILABLE")
        self.assertIsNotNone(by_code["501001"]["static_premium_rate"])

        # Missing source data must not delete the LOF from the all-market table.
        self.assertEqual(by_code["161128"]["quote_status"], "UNAVAILABLE")
        self.assertEqual(by_code["161128"]["official_nav_status"], "UNAVAILABLE")
        self.assertIsNone(by_code["161128"]["static_premium_rate"])

    def test_old_quote_is_stale_not_fresh(self) -> None:
        quotes = [
            QuoteRecord(
                code="501001",
                exchange="SSE",
                name="财通精选混合LOF",
                price=Decimal("1.429"),
                quote_time=datetime(2026, 9, 29, 11, 28, 0, tzinfo=TZ),
                pct_change=None,
                volume=None,
                amount=None,
                source="TENCENT_QUOTE",
            )
        ]
        snapshot = build_market_snapshot(
            universe=[self.universe[0]],
            quotes=quotes,
            official_navs=[],
            generated_at=self.cutoff,
            market_cutoff=self.cutoff,
            max_quote_age_seconds=30,
            snapshot_id="stale-snapshot",
        )
        self.assertEqual(snapshot["rows"][0]["quote_status"], "STALE")

    def test_validator_rejects_missing_full_market_row(self) -> None:
        snapshot = {
            "contract_version": CONTRACT_VERSION,
            "snapshot_id": "bad",
            "generated_at": self.cutoff,
            "market_cutoff": self.cutoff,
            "universe_count": 2,
            "rows": [],
            "quality_summary": {
                "quote_fresh_count": 0,
                "quote_stale_count": 0,
                "quote_unavailable_count": 0,
                "official_nav_available_count": 0,
                "official_nav_unavailable_count": 0,
                "estimated_nav_available_count": 0,
                "row_count": 0,
            },
        }
        with self.assertRaises(ValueError):
            validate_market_snapshot(snapshot, max_quote_age_seconds=30)


if __name__ == "__main__":
    unittest.main()
