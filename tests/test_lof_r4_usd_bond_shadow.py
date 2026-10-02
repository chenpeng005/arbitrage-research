import json
import tempfile
import unittest
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from runtime.lof.r4_usd_bond_shadow import resolve_501300_usd_bond_shadow
from runtime.lof.r4_usd_bond_shadow_runner import (
    calculate_shadow_row,
    collect_once,
)
from runtime.lof.snapshot_store import LofSnapshotStore
from runtime.lof.wscn_market_proxy import (
    WscnMarketProxyQuote,
    parse_wscn_market_proxy,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def _quote(
    prod_code: str,
    current: str,
    previous_close: str,
    quote_time: datetime,
) -> WscnMarketProxyQuote:
    return WscnMarketProxyQuote(
        prod_code=prod_code,
        symbol=prod_code.split(".", 1)[0],
        name=prod_code,
        current=Decimal(current),
        previous_close=Decimal(previous_close),
        quote_time=quote_time,
        source="WSCN_MARKET_REAL",
        error=None,
    )


def _main_snapshot(
    *,
    quote_status: str = "FRESH",
    nav_lag: str = "T-1",
) -> dict:
    return {
        "snapshot_id": "main-501300-test",
        "rows": [{
            "code": "501300",
            "name": "美元债LOF",
            "price": 0.930,
            "quote_status": quote_status,
            "quote_time": "2026-09-30T14:00:00+08:00",
            "official_nav": 0.910,
            "official_nav_date": "2026-09-29",
            "official_nav_lag_label": nav_lag,
        }],
    }


class R4UsdBondShadowTests(unittest.TestCase):
    def test_parse_wscn_market_proxy_derives_previous_close(self):
        quote_time = datetime(2026, 10, 2, 11, 0, tzinfo=SHANGHAI_TZ)
        payload = {
            "data": {
                "fields": [
                    "symbol",
                    "prod_name",
                    "last_px",
                    "px_change",
                    "update_time",
                ],
                "snapshot": {
                    "US10YR.OTC": [
                        "US10YR",
                        "美国10年期国债收益率",
                        5.251,
                        0.015,
                        int(quote_time.timestamp()),
                    ]
                },
            },
        }
        quote = parse_wscn_market_proxy(
            payload,
            prod_code="US10YR.OTC",
        )
        self.assertIsNone(quote.error)
        self.assertEqual(quote.current, Decimal("5.251"))
        self.assertEqual(quote.previous_close, Decimal("5.236"))
        self.assertEqual(quote.quote_time, quote_time)

    def test_transparent_duration_formula(self):
        now = datetime(2026, 9, 30, 14, 0, tzinfo=SHANGHAI_TZ)
        result = resolve_501300_usd_bond_shadow(
            official_nav=Decimal("0.9100"),
            official_nav_lag_label="T-1",
            us10y=_quote(
                "US10YR.OTC",
                "4.015",
                "4.000",
                datetime(2026, 9, 30, 13, 55, tzinfo=SHANGHAI_TZ),
            ),
            usdcny=_quote(
                "USDCNY.OTC",
                "7.0100",
                "7.0000",
                datetime(2026, 9, 30, 13, 56, tzinfo=SHANGHAI_TZ),
            ),
            as_of=now,
        )
        expected_bond = (
            Decimal("0.90")
            * Decimal("-5")
            * Decimal("0.015")
            / Decimal("100")
        )
        expected_fx = Decimal("7.0100") / Decimal("7.0000") - Decimal("1")
        expected_nav = (
            Decimal("0.9100")
            * (Decimal("1") + expected_bond)
            * (Decimal("1") + expected_fx)
        )
        self.assertEqual(result.shadow_status, "AVAILABLE")
        self.assertEqual(result.bond_return, expected_bond)
        self.assertEqual(result.fx_return, expected_fx)
        self.assertEqual(result.estimated_nav, expected_nav)
        self.assertFalse(result.eligible_for_main)

    def test_official_nav_not_t1_fails_closed(self):
        now = datetime(2026, 10, 2, 14, 0, tzinfo=SHANGHAI_TZ)
        fresh = datetime(2026, 10, 2, 13, 55, tzinfo=SHANGHAI_TZ)
        result = resolve_501300_usd_bond_shadow(
            official_nav=Decimal("0.914"),
            official_nav_lag_label="T-2",
            us10y=_quote("US10YR.OTC", "5.25", "5.23", fresh),
            usdcny=_quote("USDCNY.OTC", "6.70", "6.69", fresh),
            as_of=now,
        )
        self.assertEqual(result.shadow_status, "UNAVAILABLE")
        self.assertTrue(result.error.startswith("OFFICIAL_NAV_NOT_T1"))
        self.assertIsNone(result.estimated_nav)

    def test_stale_fx_fails_closed(self):
        now = datetime(2026, 10, 2, 14, 0, tzinfo=SHANGHAI_TZ)
        result = resolve_501300_usd_bond_shadow(
            official_nav=Decimal("0.914"),
            official_nav_lag_label="T-1",
            us10y=_quote(
                "US10YR.OTC",
                "5.25",
                "5.23",
                datetime(2026, 10, 2, 13, 55, tzinfo=SHANGHAI_TZ),
            ),
            usdcny=_quote(
                "USDCNY.OTC",
                "6.70",
                "6.69",
                datetime(2026, 10, 1, 3, 0, tzinfo=SHANGHAI_TZ),
            ),
            as_of=now,
        )
        self.assertEqual(result.shadow_status, "STALE")
        self.assertEqual(result.error, "STALE_INPUT_QUOTE")
        self.assertIsNone(result.estimated_nav)

    def test_stale_main_market_blocks_premium_only(self):
        now = datetime(2026, 9, 30, 14, 0, tzinfo=SHANGHAI_TZ)
        fresh = datetime(2026, 9, 30, 13, 55, tzinfo=SHANGHAI_TZ)
        row = calculate_shadow_row(
            main_snapshot=_main_snapshot(quote_status="STALE"),
            us10y=_quote("US10YR.OTC", "4.01", "4.00", fresh),
            usdcny=_quote("USDCNY.OTC", "7.01", "7.00", fresh),
            as_of=now,
        )
        self.assertEqual(row["status"], "AVAILABLE")
        self.assertIsNotNone(row["shadow_estimated_nav"])
        self.assertIsNone(row["shadow_premium_rate"])

    def test_available_shadow_persists_separate_snapshot(self):
        now = datetime(2026, 9, 30, 14, 0, tzinfo=SHANGHAI_TZ)
        us10y = _quote(
            "US10YR.OTC",
            "4.015",
            "4.000",
            datetime(2026, 9, 30, 13, 55, tzinfo=SHANGHAI_TZ),
        )
        usdcny = _quote(
            "USDCNY.OTC",
            "7.010",
            "7.000",
            datetime(2026, 9, 30, 13, 56, tzinfo=SHANGHAI_TZ),
        )
        with tempfile.TemporaryDirectory() as main_root, tempfile.TemporaryDirectory() as state_root:
            LofSnapshotStore(main_root).persist(_main_snapshot())

            def fake_fetch(code, **kwargs):
                return us10y if code == "US10YR.OTC" else usdcny

            with patch(
                "runtime.lof.r4_usd_bond_shadow_runner.fetch_wscn_market_proxy",
                side_effect=fake_fetch,
            ):
                snapshot = collect_once(
                    main_data_root=main_root,
                    state_root=state_root,
                    now=now,
                )

            row = snapshot["rows"][0]
            self.assertEqual(row["status"], "AVAILABLE")
            self.assertEqual(row["quality"], "LOW")
            self.assertIsNotNone(row["shadow_estimated_nav"])
            self.assertIsNotNone(row["shadow_premium_rate"])
            self.assertFalse(row["eligible_for_main"])
            latest = Path(state_root) / "r4_usd_bond_501300_shadow.json"
            self.assertTrue(latest.exists())
            persisted = json.loads(latest.read_text(encoding="utf-8"))
            self.assertEqual(
                persisted["source_market_snapshot_id"],
                "main-501300-test",
            )


if __name__ == "__main__":
    unittest.main()
