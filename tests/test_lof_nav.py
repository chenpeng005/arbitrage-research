from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from runtime.lof.nav import (
    OfficialNavRecord,
    is_nav_stale,
    nav_age_days,
    parse_sse_nav_payload,
    parse_szse_nav_payload,
)


class LofOfficialNavTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fetched_at = datetime(2026, 9, 29, 3, 0, tzinfo=timezone.utc)

    def test_parse_sse_bulk_nav(self) -> None:
        payload = {
            "pageHelp": {
                "data": [
                    {
                        "FUND_CODE": "501001",
                        "NAV": "1.445",
                        "ASSESS_DATE": "2026-09-28",
                    },
                    {
                        "FUND_CODE": "501999",
                        "NAV": "",
                        "ASSESS_DATE": "2026-09-28",
                    },
                ]
            }
        }
        rows = parse_sse_nav_payload(payload, fetched_at=self.fetched_at)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].nav, Decimal("1.445"))
        self.assertEqual(rows[0].nav_date, date(2026, 9, 28))
        self.assertTrue(rows[0].available)
        self.assertEqual(rows[1].error, "INVALID_OR_MISSING_NAV")

    def test_parse_szse_uses_first_valid_requested_code(self) -> None:
        payload = [
            {
                "data": [
                    {
                        "nav_date": "2026-09-28",
                        "fund_code": "999999",
                        "security_short_name": "wrong",
                        "nav_per_share": "9.99",
                    },
                    {
                        "nav_date": "2026-09-28",
                        "fund_code": "161128",
                        "security_short_name": "标普信息科技LOF",
                        "nav_per_share": "7.0123",
                    },
                ]
            }
        ]
        row = parse_szse_nav_payload(
            payload,
            requested_code="161128",
            fetched_at=self.fetched_at,
        )
        self.assertEqual(row.nav, Decimal("7.0123"))
        self.assertEqual(row.nav_date, date(2026, 9, 28))
        self.assertTrue(row.available)

    def test_szse_empty_payload_returns_explicit_missing_record(self) -> None:
        row = parse_szse_nav_payload(
            [],
            requested_code="161128",
            fetched_at=self.fetched_at,
        )
        self.assertIsNone(row.nav)
        self.assertEqual(row.error, "NO_NAV_DATA")

    def test_nav_age_and_stale_are_explicitly_configurable(self) -> None:
        row = OfficialNavRecord(
            code="161128",
            exchange="SZSE",
            nav=Decimal("7.0"),
            nav_date=date(2026, 9, 28),
            fetched_at=self.fetched_at,
            source="SZSE_OFFICIAL",
        )
        self.assertEqual(nav_age_days(row, as_of=date(2026, 9, 29)), 1)
        self.assertFalse(
            is_nav_stale(
                row,
                as_of=date(2026, 9, 29),
                max_age_calendar_days=1,
            )
        )
        self.assertTrue(
            is_nav_stale(
                row,
                as_of=date(2026, 9, 30),
                max_age_calendar_days=1,
            )
        )

    def test_missing_nav_is_stale(self) -> None:
        row = OfficialNavRecord(
            code="501001",
            exchange="SSE",
            nav=None,
            nav_date=None,
            fetched_at=self.fetched_at,
            source="SSE_OFFICIAL",
            error="MISSING",
        )
        self.assertTrue(
            is_nav_stale(
                row,
                as_of=date(2026, 9, 29),
                max_age_calendar_days=1,
            )
        )


if __name__ == "__main__":
    unittest.main()
