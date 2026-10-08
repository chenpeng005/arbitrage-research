import gzip
import json
from pathlib import Path

from runtime.etf_primary.comparison_view import build_comparison, build_from_state_dir


def _row(code, total, account, *, kind="NET", account_kind="NET", allowed=True):
    return {
        "exchange": "SZSE",
        "code": code,
        "creation_allowed": allowed,
        "total_baskets": total,
        "account_baskets": account,
        "market_capacity_kind": kind,
        "account_capacity_kind": account_kind,
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
