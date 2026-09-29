from __future__ import annotations

import json
import tempfile
import unittest
from unittest.mock import patch
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from runtime.lof.universe import LofIdentity

from runtime.lof.nav import (
    OfficialNavRecord,
    fetch_all_official_nav,
    is_nav_stale,
    load_official_nav_fixture,
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

    @patch(
        "runtime.lof.nav.fetch_sse_official_nav",
        side_effect=RuntimeError("SSE blocked"),
    )
    @patch("runtime.lof.nav.fetch_szse_official_nav")
    def test_sse_failure_does_not_delete_szse_nav(
        self,
        szse_mock,
        sse_mock,
    ) -> None:
        szse_mock.return_value = [
            OfficialNavRecord(
                code="161128",
                exchange="SZSE",
                nav=Decimal("7.0123"),
                nav_date=date(2026, 9, 28),
                fetched_at=self.fetched_at,
                source="SZSE_OFFICIAL",
            )
        ]
        universe = [
            LofIdentity(
                code="501001",
                name="沪市LOF",
                exchange="SSE",
            ),
            LofIdentity(
                code="161128",
                name="深市LOF",
                exchange="SZSE",
            ),
        ]

        rows = fetch_all_official_nav(universe)
        by_code = {row.code: row for row in rows}

        self.assertFalse(by_code["501001"].available)
        self.assertEqual(
            by_code["501001"].error,
            "FETCH_ERROR:RuntimeError",
        )
        self.assertTrue(by_code["161128"].available)
        self.assertEqual(by_code["161128"].nav, Decimal("7.0123"))

    def test_load_official_nav_fixture(self) -> None:
        payload = {
            "fetched_at": "2026-09-29T13:35:00+08:00",
            "rows": [
                {
                    "code": "501047",
                    "exchange": "SSE",
                    "nav": "1.051",
                    "nav_date": "2026-09-28",
                    "source": "SSE_OFFICIAL_FIXTURE",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nav.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = load_official_nav_fixture(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "501047")
        self.assertEqual(rows[0].nav, Decimal("1.051"))
        self.assertEqual(rows[0].nav_date, date(2026, 9, 28))
        self.assertTrue(rows[0].available)

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
