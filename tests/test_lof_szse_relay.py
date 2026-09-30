from __future__ import annotations

import hashlib
import json
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from http.client import RemoteDisconnected
from unittest.mock import patch

from runtime.lof.nav import OfficialNavRecord, _szse_nav_from_relay
from runtime.lof.source_preflight import evaluate_preflight
from runtime.lof.szse_relay import (
    DEFAULT_SZSE_RELAY_BASE_URL,
    SzseRelayBundle,
    fetch_szse_relay_bundle,
    validate_szse_relay_freshness,
)
from runtime.lof.szse_relay_publish import build_relay_payloads
from runtime.lof.universe import LofIdentity, fetch_all_lof_universe


def _bundle(now: datetime, count: int = 200) -> SzseRelayBundle:
    universe_rows = [
        {
            "code": f"16{i:04d}",
            "name": f"LOF{i}",
            "manager": "TEST",
        }
        for i in range(count)
    ]
    nav_rows = [
        {
            "code": f"16{i:04d}",
            "nav": "1.234",
            "nav_date": "2026-09-29",
        }
        for i in range(count)
    ]
    return SzseRelayBundle(
        base_url="https://relay.test/lof_relay",
        manifest={
            "relay_version": "lof-szse-official-relay-v1",
            "source": "SZSE_OFFICIAL",
            "fetched_at": now.isoformat(),
            "universe_count": count,
            "nav_count": count,
        },
        universe={
            "source": "SZSE_OFFICIAL",
            "fetched_at": now.isoformat(),
            "rows": universe_rows,
        },
        nav={
            "source": "SZSE_OFFICIAL",
            "fetched_at": now.isoformat(),
            "rows": nav_rows,
        },
        manifest_sha256="a" * 64,
    )


class LofSzseRelayTest(unittest.TestCase):
    @patch("runtime.lof.szse_relay._fetch_github_contents_bytes")
    @patch("runtime.lof.szse_relay._resolve_default_relay_commit")
    def test_default_relay_pins_all_files_to_one_commit(
        self,
        ref_mock,
        content_mock,
    ) -> None:
        commit = "a" * 40
        ref_mock.return_value = commit
        universe = {
            "source": "SZSE_OFFICIAL",
            "rows": [
                {"code": f"16{i:04d}", "name": f"LOF{i}"}
                for i in range(200)
            ],
        }
        nav = {
            "source": "SZSE_OFFICIAL",
            "rows": [
                {"code": f"16{i:04d}", "nav": "1.0", "nav_date": "2026-09-29"}
                for i in range(200)
            ],
        }
        universe_bytes = json.dumps(
            universe,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        nav_bytes = json.dumps(
            nav,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        manifest = {
            "relay_version": "lof-szse-official-relay-v1",
            "source": "SZSE_OFFICIAL",
            "fetched_at": "2026-09-29T07:30:00+00:00",
            "universe_count": 200,
            "nav_count": 200,
            "files": {
                "universe": {
                    "filename": "szse_universe.json",
                    "sha256": hashlib.sha256(universe_bytes).hexdigest(),
                },
                "nav": {
                    "filename": "szse_nav.json",
                    "sha256": hashlib.sha256(nav_bytes).hexdigest(),
                },
            },
        }
        manifest_bytes = json.dumps(
            manifest,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        def get_bytes(filename, *, timeout, ref):
            self.assertEqual(ref, commit)
            return {
                "manifest.json": manifest_bytes,
                "szse_universe.json": universe_bytes,
                "szse_nav.json": nav_bytes,
            }[filename]

        content_mock.side_effect = get_bytes
        bundle = fetch_szse_relay_bundle(
            DEFAULT_SZSE_RELAY_BASE_URL,
            timeout=20,
        )
        self.assertEqual(len(bundle.universe["rows"]), 200)
        self.assertEqual(len(bundle.nav["rows"]), 200)
        ref_mock.assert_called_once()
        self.assertEqual(content_mock.call_count, 3)

    @patch("runtime.lof.szse_relay._fetch_relay_file")
    @patch("runtime.lof.szse_relay._resolve_default_relay_commit")
    def test_default_relay_rechecks_branch_head_between_calls(
        self,
        ref_mock,
        file_mock,
    ) -> None:
        ref_mock.side_effect = ["a" * 40, "b" * 40]
        universe = {
            "source": "SZSE_OFFICIAL",
            "rows": [
                {"code": f"16{i:04d}", "name": f"LOF{i}"}
                for i in range(200)
            ],
        }
        nav = {
            "source": "SZSE_OFFICIAL",
            "rows": [
                {"code": f"16{i:04d}", "nav": "1.0", "nav_date": "2026-09-29"}
                for i in range(200)
            ],
        }
        universe_bytes = json.dumps(
            universe, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        nav_bytes = json.dumps(
            nav, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        manifest = {
            "relay_version": "lof-szse-official-relay-v1",
            "source": "SZSE_OFFICIAL",
            "fetched_at": "2026-09-30T03:15:43+00:00",
            "universe_count": 200,
            "nav_count": 200,
            "files": {
                "universe": {
                    "filename": "szse_universe.json",
                    "sha256": hashlib.sha256(universe_bytes).hexdigest(),
                },
                "nav": {
                    "filename": "szse_nav.json",
                    "sha256": hashlib.sha256(nav_bytes).hexdigest(),
                },
            },
        }
        manifest_bytes = json.dumps(
            manifest, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")

        pinned_refs: list[str | None] = []

        def get_file(base_url, filename, *, timeout, pinned_ref=None):
            pinned_refs.append(pinned_ref)
            return {
                "manifest.json": manifest_bytes,
                "szse_universe.json": universe_bytes,
                "szse_nav.json": nav_bytes,
            }[filename]

        file_mock.side_effect = get_file
        fetch_szse_relay_bundle(DEFAULT_SZSE_RELAY_BASE_URL, timeout=20)
        fetch_szse_relay_bundle(DEFAULT_SZSE_RELAY_BASE_URL, timeout=20)

        self.assertEqual(ref_mock.call_count, 2)
        self.assertEqual(pinned_refs[:3], ["a" * 40] * 3)
        self.assertEqual(pinned_refs[3:], ["b" * 40] * 3)

    def test_stale_relay_fails_closed(self) -> None:
        now = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
        bundle = _bundle(now - timedelta(days=3))
        with self.assertRaises(ValueError):
            validate_szse_relay_freshness(
                bundle,
                as_of=now,
                max_age_seconds=48 * 60 * 60,
            )

    @patch("runtime.lof.universe.fetch_szse_relay_bundle")
    @patch("runtime.lof.universe.fetch_szse_universe")
    @patch("runtime.lof.universe.fetch_sse_universe")
    def test_direct_universe_failure_uses_official_relay(
        self,
        sse_mock,
        szse_mock,
        relay_mock,
    ) -> None:
        now = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
        sse_mock.return_value = [
            LofIdentity(code="501001", name="SSE", exchange="SSE")
        ]
        szse_mock.side_effect = RemoteDisconnected("blocked")
        relay_mock.return_value = _bundle(now)

        rows = fetch_all_lof_universe(
            timeout=1,
            szse_relay_base_url="https://relay.test/lof_relay",
            as_of=now,
        )

        self.assertEqual(len(rows), 201)
        szse = [row for row in rows if row.exchange == "SZSE"]
        self.assertTrue(szse)
        self.assertTrue(
            all(row.source == "SZSE_OFFICIAL_RELAY" for row in szse)
        )

    def test_relay_nav_keeps_official_relay_provenance(self) -> None:
        now = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
        rows = _szse_nav_from_relay(
            _bundle(now),
            codes=["160000", "160001"],
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row.available for row in rows))
        self.assertTrue(
            all(row.source == "SZSE_OFFICIAL_RELAY" for row in rows)
        )
        self.assertEqual(rows[0].nav, Decimal("1.234"))

    def test_publisher_normalizes_official_data(self) -> None:
        now = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
        universe = [
            LofIdentity(
                code=f"16{i:04d}",
                name=f"LOF{i}",
                exchange="SZSE",
                source="SZSE_OFFICIAL",
            )
            for i in range(200)
        ]
        nav_rows = [
            OfficialNavRecord(
                code=f"16{i:04d}",
                exchange="SZSE",
                nav=Decimal("1.1"),
                nav_date=date(2026, 9, 29),
                fetched_at=now,
                source="SZSE_OFFICIAL",
            )
            for i in range(200)
        ]
        universe_payload, nav_payload = build_relay_payloads(
            universe=universe,
            nav_rows=nav_rows,
            fetched_at=now,
        )
        self.assertEqual(len(universe_payload["rows"]), 200)
        self.assertEqual(len(nav_payload["rows"]), 200)
        self.assertEqual(nav_payload["source"], "SZSE_OFFICIAL")

    def test_publisher_rejects_nav_coverage_below_90_percent(self) -> None:
        now = datetime(2026, 9, 29, 8, tzinfo=timezone.utc)
        universe = [
            LofIdentity(
                code=f"16{i:04d}",
                name=f"LOF{i}",
                exchange="SZSE",
                source="SZSE_OFFICIAL",
            )
            for i in range(200)
        ]
        nav_rows = [
            OfficialNavRecord(
                code=f"16{i:04d}",
                exchange="SZSE",
                nav=Decimal("1.1"),
                nav_date=date(2026, 9, 29),
                fetched_at=now,
                source="SZSE_OFFICIAL",
            )
            for i in range(179)
        ]
        with self.assertRaises(ValueError):
            build_relay_payloads(
                universe=universe,
                nav_rows=nav_rows,
                fetched_at=now,
            )

    def test_preflight_reports_relay_transport_without_downgrading(self) -> None:
        now = datetime(2026, 9, 29, 15, 30, tzinfo=timezone.utc)
        snapshot = {
            "collector_status": "PASS",
            "universe_count": 400,
            "rows": [{"code": str(i)} for i in range(400)],
            "quality_summary": {
                "quote_fresh_count": 0,
                "quote_stale_count": 398,
                "quote_unavailable_count": 2,
                "official_nav_available_count": 390,
                "state_available_count": 395,
                "estimated_nav_available_count": 100,
                "estimated_nav_stale_count": 0,
                "estimated_nav_unavailable_count": 300,
            },
            "lane_errors": {},
        }
        result = evaluate_preflight(
            snapshot=snapshot,
            r1_context_resolved=120,
            r1_context_unresolved=10,
            expect_fresh_quotes=False,
            application_commit_sha="b" * 40,
            checked_at=now,
            source_transport={
                "szse_universe_transport": "OFFICIAL_RELAY",
                "szse_nav_transport": "OFFICIAL_RELAY",
                "relay_fetched_at": now.isoformat(),
                "relay_manifest_sha256": "c" * 64,
            },
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(
            result["source_transport"]["szse_universe_transport"],
            "OFFICIAL_RELAY",
        )
        self.assertEqual(
            result["source_transport"]["szse_nav_transport"],
            "OFFICIAL_RELAY",
        )


if __name__ == "__main__":
    unittest.main()
