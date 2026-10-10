import gzip
import json
from pathlib import Path

from runtime.etf_primary.comparison_view import build_comparison, build_from_state_dir


def _row(
    code, total, account, *, kind="NET", account_kind="NET", allowed=True,
    market_status="LIMITED", account_status="LIMITED",
):
    return {
        "exchange": "SZSE",
        "code": code,
        "creation_allowed": allowed,
        "total_baskets": total,
        "account_baskets": account,
        "market_capacity_kind": kind,
        "account_capacity_kind": account_kind,
        "market_capacity_status": market_status,
        "account_capacity_status": account_status,
        "basket_value": 2_000_000.0,
        "creation_redemption_unit": 1_000_000,
    }


def test_build_comparison_surfaces_basket_jump():
    previous = {"trade_date": "20260929", "rows": [_row("159501", 1, 1)]}
    current = {"trade_date": "20260930", "rows": [_row("159501", 180, 1)]}

    payload = build_comparison(previous, current)
    row = payload["rows"][0]

    assert payload["previous_trade_date"] == "20260929"
    assert payload["current_trade_date"] == "20260930"
    assert payload["changed_count"] == 1
    assert row["capacity_comparable"] is True
    assert row["basket_delta"] == 179
    assert row["basket_ratio"] == 180.0
    assert row["previous"]["total_baskets"] == 1
    assert row["current"]["total_baskets"] == 180
    assert row["previous"]["market_capacity_status"] == "LIMITED"
    assert row["current"]["market_capacity_status"] == "LIMITED"


def test_capacity_rule_change_is_not_numeric_comparison():
    previous = {"trade_date": "20260929", "rows": [_row("159501", 1, 1, kind="CUMULATIVE")]}
    current = {"trade_date": "20260930", "rows": [_row("159501", 180, 1, kind="NET")]}

    row = build_comparison(previous, current)["rows"][0]
    assert row["capacity_rule_changed"] is True
    assert row["capacity_comparable"] is False
    assert row["basket_delta"] is None
    assert row["basket_ratio"] is None


def test_build_from_state_dir_uses_latest_two_trade_dates(tmp_path: Path):
    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    payloads = [
        ("20260929", [_row("159501", 1, 1)]),
        ("20260930", [_row("159501", 180, 1)]),
    ]
    for trade_date, rows in payloads:
        with gzip.open(snapshots / f"{trade_date}.json.gz", "wt", encoding="utf-8") as handle:
            json.dump({"trade_date": trade_date, "rows": rows}, handle)

    payload = build_from_state_dir(tmp_path)
    assert payload["previous_trade_date"] == "20260929"
    assert payload["current_trade_date"] == "20260930"
    assert payload["rows"][0]["basket_ratio"] == 180.0


def test_unlimited_to_limited_is_explicit_status_change():
    previous = {
        "trade_date": "20261008",
        "rows": [_row("159501", None, None, kind=None, account_kind=None, market_status="UNLIMITED", account_status="UNLIMITED")],
    }
    current = {
        "trade_date": "20261009",
        "rows": [_row("159501", 5, 1, kind="NET", account_kind="NET", market_status="LIMITED", account_status="LIMITED")],
    }
    row = build_comparison(previous, current)["rows"][0]
    assert row["previous"]["market_capacity_status"] == "UNLIMITED"
    assert row["current"]["market_capacity_status"] == "LIMITED"
    assert row["capacity_status_comparable"] is True
    assert row["capacity_status_changed"] is True
    assert row["account_capacity_status_changed"] is True
    assert row["capacity_comparable"] is False
    assert row["changed"] is True


def test_limited_to_unlimited_is_explicit_status_change():
    previous = {
        "trade_date": "20261008",
        "rows": [_row("159501", 5, 1, market_status="LIMITED", account_status="LIMITED")],
    }
    current = {
        "trade_date": "20261009",
        "rows": [_row("159501", None, None, kind=None, account_kind=None, market_status="UNLIMITED", account_status="UNLIMITED")],
    }
    row = build_comparison(previous, current)["rows"][0]
    assert row["previous"]["market_capacity_status"] == "LIMITED"
    assert row["current"]["market_capacity_status"] == "UNLIMITED"
    assert row["capacity_status_comparable"] is True
    assert row["capacity_status_changed"] is True
    assert row["changed"] is True


def test_legacy_null_capacity_is_unknown_not_assumed_unlimited():
    legacy = {
        "trade_date": "20261008",
        "rows": [{
            "exchange": "SZSE",
            "code": "159501",
            "creation_allowed": True,
            "total_baskets": None,
            "account_baskets": None,
            "market_capacity_kind": None,
            "account_capacity_kind": None,
        }],
    }
    current = {
        "trade_date": "20261009",
        "rows": [_row("159501", None, None, kind=None, account_kind=None, market_status="UNLIMITED", account_status="UNLIMITED")],
    }
    row = build_comparison(legacy, current)["rows"][0]
    assert row["previous"]["market_capacity_status"] == "UNKNOWN"
    assert row["current"]["market_capacity_status"] == "UNLIMITED"
    assert row["capacity_status_comparable"] is False
    assert row["capacity_status_changed"] is False
    assert row["changed"] is False
