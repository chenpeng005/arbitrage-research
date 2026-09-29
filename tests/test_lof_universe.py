from __future__ import annotations

from http.client import RemoteDisconnected
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.lof.universe import (
    _get_json,
    fetch_all_lof_universe,
    load_sse_universe_fixture,
    load_szse_universe_fixture,
    parse_sse_universe,
    parse_szse_universe_page,
)


class LofUniverseParserTest(unittest.TestCase):
    def test_parse_sse_universe(self) -> None:
        payload = {
            "result": [
                {
                    "fundCode": "501001",
                    "secNameFull": "财通精选混合LOF",
                    "companyName": "财通基金管理有限公司",
                    "listingDate": "20150925",
                },
                {
                    "fundCode": "",
                    "secNameFull": "坏数据",
                },
            ]
        }
        rows = parse_sse_universe(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "501001")
        self.assertEqual(rows[0].exchange, "SSE")
        self.assertEqual(rows[0].source, "SSE_OFFICIAL")

    def test_parse_szse_universe_page_strips_html(self) -> None:
        payload = [
            {
                "metadata": {"pagecount": 1},
                "data": [
                    {
                        "sys_key": "<a><u>161128</u></a>",
                        "kzjcurl": "<a><u>标普信息科技LOF</u></a>",
                        "glrmc": "易方达基金管理有限公司",
                    }
                ],
            }
        ]
        rows = parse_szse_universe_page(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "161128")
        self.assertEqual(rows[0].name, "标普信息科技LOF")
        self.assertEqual(rows[0].exchange, "SZSE")
        self.assertEqual(rows[0].source, "SZSE_OFFICIAL")

    def test_empty_payload_is_safe(self) -> None:
        self.assertEqual(parse_szse_universe_page([]), [])

    def test_explicit_sse_fixture_loader(self) -> None:
        payload = {
            "as_of": "2026-09-29",
            "count": 1,
            "rows": [
                {
                    "code": "501001",
                    "name": "财通精选混合LOF",
                    "exchange": "SSE",
                    "manager": "财通基金",
                    "listing_date": "20150925",
                    "source": "SSE_OFFICIAL_FIXTURE",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sse.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = load_sse_universe_fixture(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "501001")
        self.assertEqual(rows[0].source, "SSE_OFFICIAL_FIXTURE")

    def test_explicit_szse_fixture_loader(self) -> None:
        payload = {
            "as_of": "2026-09-29",
            "count": 1,
            "rows": [
                {
                    "code": "161128",
                    "name": "标普信息科技LOF",
                    "exchange": "SZSE",
                    "manager": "易方达基金",
                    "source": "SZSE_OFFICIAL_FIXTURE",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "szse.json"
            path.write_text(
                json.dumps(payload, ensure_ascii=False),
                encoding="utf-8",
            )
            rows = load_szse_universe_fixture(path)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].code, "161128")
        self.assertEqual(rows[0].exchange, "SZSE")
        self.assertEqual(rows[0].source, "SZSE_OFFICIAL_FIXTURE")

    def test_get_json_retries_transient_disconnect(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, exc_type, exc, tb):
                return False
            def read(self):
                return b'{"ok": true}'

        with patch(
            "runtime.lof.universe.urlopen",
            side_effect=[
                RemoteDisconnected("first attempt"),
                FakeResponse(),
            ],
        ) as mocked:
            payload = _get_json(
                "https://unit.test/data",
                {"x": 1},
                referer="https://unit.test/",
                timeout=1,
                retries=2,
                backoff_seconds=0,
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(mocked.call_count, 2)

    def test_fetch_all_is_not_unit_tested_against_live_network(self) -> None:
        # Keep CI deterministic. Live-source smoke tests belong to deployment /
        # runtime preflight, not unit tests.
        self.assertTrue(callable(fetch_all_lof_universe))


if __name__ == "__main__":
    unittest.main()
