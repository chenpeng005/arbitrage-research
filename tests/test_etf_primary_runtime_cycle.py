import json
from datetime import datetime, timezone

import runtime.etf_primary.runtime_cycle as runtime_cycle
from runtime.etf_primary.szse_relay import SzseEtfRelayBundle


def _row(code: str, *, total_limit: int, account_limit: int = 1000000) -> dict:
    return {
        "code": code,
        "exchange": "SZSE",
        "trade_date": "20261009",
        "creation_allowed": True,
        "redemption_allowed": True,
        "creation_redemption_unit": 1000000,
        "creation_limit": total_limit,
        "account_creation_limit": account_limit,
    }


def _bundle(trade_date: str, rows: list[dict]) -> SzseEtfRelayBundle:
    normalized = []
    for row in rows:
        item = dict(row)
        item["trade_date"] = trade_date
        normalized.append(item)
    return SzseEtfRelayBundle(
        base_url="https://example.invalid",
        manifest={
            "relay_version": "etf-primary-official-relay-v2",
            "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
            "fetched_at": "2026-10-09T00:00:00+00:00",
            "target_trade_date": trade_date,
        },
        universe={"rows": []},
        manifest_sha256="x",
        full_universe={"rows": []},
        pcf_snapshot={
            "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
            "target_trade_date": trade_date,
            "universe_count": len(normalized),
            "pcf_found_count": len(normalized),
            "latest_trade_date_count": len(normalized),
            "coverage": {},
            "missing": {},
            "stale": [],
            "rows": normalized,
        },
    )


def test_build_daily_diff_detects_capacity_and_distribution_change():
    previous = [_row("159501", total_limit=5_000_000)]
    current = [_row("159501", total_limit=50_000_000)]
    _, events = runtime_cycle.build_daily_diff(
        previous_rows=previous,
        current_rows=current,
        trade_date="20261009",
        source="SSE_OFFICIAL+SZSE_OFFICIAL",
        relay_fetched_at="2026-10-09T00:00:00+00:00",
    )
    assert [event["event_type"] for event in events] == [
        "TOTAL_CAPACITY_JUMP",
        "ACCOUNT_DISTRIBUTION_IMPROVED",
    ]
    assert events[0]["previous_value"] == 5.0
    assert events[0]["current_value"] == 50.0
    assert events[1]["previous_value"] == 5
    assert events[1]["current_value"] == 50


def test_run_cycle_bootstrap_then_diff(tmp_path, monkeypatch):
    rows = [_row(f"159{i:03d}", total_limit=5_000_000) for i in range(1000)]
    first = _bundle("20261008", rows)
    second_rows = [dict(row) for row in rows]
    second_rows[0]["creation_limit"] = 50_000_000
    second = _bundle("20261009", second_rows)
    bundles = iter([first, second])

    monkeypatch.setattr(runtime_cycle, "fetch_etf_primary_relay_bundle", lambda *args, **kwargs: next(bundles))
    monkeypatch.setattr(runtime_cycle, "validate_relay_freshness", lambda *args, **kwargs: 60)

    baseline = runtime_cycle.run_cycle(
        state_dir=tmp_path,
        relay_base_url="https://example.invalid",
        now=datetime(2026, 10, 8, 1, 0, tzinfo=timezone.utc),
    )
    assert baseline["status"] == "BASELINE_CREATED"
    assert baseline["event_count"] == 0
    assert (tmp_path / "snapshots" / "20261008.json.gz").exists()

    diff = runtime_cycle.run_cycle(
        state_dir=tmp_path,
        relay_base_url="https://example.invalid",
        now=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc),
    )
    assert diff["status"] == "DIFF_COMPLETED"
    assert diff["event_count"] == 2
    events = json.loads((tmp_path / "events" / "20261009.json").read_text(encoding="utf-8"))
    assert [event["event_type"] for event in events["events"]] == [
        "TOTAL_CAPACITY_JUMP",
        "ACCOUNT_DISTRIBUTION_IMPROVED",
    ]


def test_same_trade_date_unchanged_is_byte_stable(tmp_path, monkeypatch):
    rows = [_row(f"159{i:03d}", total_limit=5_000_000) for i in range(1000)]
    bundles = iter([_bundle("20261009", rows), _bundle("20261009", rows)])

    monkeypatch.setattr(runtime_cycle, "fetch_etf_primary_relay_bundle", lambda *args, **kwargs: next(bundles))
    monkeypatch.setattr(runtime_cycle, "validate_relay_freshness", lambda *args, **kwargs: 60)

    baseline = runtime_cycle.run_cycle(
        state_dir=tmp_path,
        relay_base_url="https://example.invalid",
        now=datetime(2026, 10, 9, 1, 0, tzinfo=timezone.utc),
    )
    assert baseline["status"] == "BASELINE_CREATED"

    archive = tmp_path / "snapshots" / "20261009.json.gz"
    current = tmp_path / "current.json"
    last_run = tmp_path / "last_run.json"
    before = (archive.read_bytes(), current.read_bytes(), last_run.read_bytes())

    unchanged = runtime_cycle.run_cycle(
        state_dir=tmp_path,
        relay_base_url="https://example.invalid",
        now=datetime(2026, 10, 9, 2, 0, tzinfo=timezone.utc),
    )
    assert unchanged["status"] == "SAME_TRADE_DATE_NO_CHANGE"
    assert unchanged["event_count"] == 0
    assert before == (archive.read_bytes(), current.read_bytes(), last_run.read_bytes())
    assert not (tmp_path / "events" / "20261009.json").exists()


def test_same_trade_date_changed_is_refresh_not_false_event(tmp_path, monkeypatch):
    rows = [_row(f"159{i:03d}", total_limit=5_000_000) for i in range(1000)]
    first = _bundle("20261009", rows)
    corrected_rows = [dict(row) for row in rows]
    corrected_rows[0]["creation_limit"] = 50_000_000
    second = _bundle("20261009", corrected_rows)
    bundles = iter([first, second])

    monkeypatch.setattr(runtime_cycle, "fetch_etf_primary_relay_bundle", lambda *args, **kwargs: next(bundles))
    monkeypatch.setattr(runtime_cycle, "validate_relay_freshness", lambda *args, **kwargs: 60)

    first_run = runtime_cycle.run_cycle(state_dir=tmp_path, relay_base_url="https://example.invalid")
    first_digest = first_run["snapshot_sha256"]
    refreshed = runtime_cycle.run_cycle(state_dir=tmp_path, relay_base_url="https://example.invalid")
    assert refreshed["status"] == "SAME_TRADE_DATE_REFRESHED"
    assert refreshed["event_count"] == 0
    assert refreshed["snapshot_sha256"] != first_digest
    assert not (tmp_path / "events" / "20261009.json").exists()
