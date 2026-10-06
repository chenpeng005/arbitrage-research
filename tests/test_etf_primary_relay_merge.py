import json
from pathlib import Path

from runtime.etf_primary.relay_merge import merge_relay_snapshot


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_merge_relay_snapshot_combines_sse_and_szse(tmp_path):
    prepare = tmp_path / "prepare"
    shards = tmp_path / "shards"
    output = tmp_path / "output"

    full_universe = {
        "rows": [
            {"exchange": "SSE", "code": "510001"},
            {"exchange": "SZSE", "code": "159001"},
            {"exchange": "SZSE", "code": "159002"},
        ]
    }
    _write(prepare / "prepare_manifest.json", {"target_trade_date": "20260930"})
    _write(prepare / "full_universe.json", full_universe)
    _write(
        prepare / "sse_pcf.json",
        {"rows": [{"exchange": "SSE", "code": "510001", "trade_date": "20260930"}]},
    )
    _write(prepare / "szse_etf_universe.json", {"rows": []})
    _write(prepare / "universe_audit.json", {"problem_counts": {}})
    _write(prepare / "szse_pcf_index.json", {"target_trade_date": "20260930", "rows": []})

    _write(
        shards / "a" / "shard-0.json",
        {
            "trade_date": "20260930",
            "shard_index": 0,
            "shard_count": 2,
            "requested_count": 1,
            "official_index_count": 1,
            "success_count": 1,
            "error_count": 0,
            "snapshots": [
                {"exchange": "SZSE", "code": "159001", "trade_date": "20260930"}
            ],
            "errors": {},
        },
    )
    _write(
        shards / "b" / "shard-1.json",
        {
            "trade_date": "20260930",
            "shard_index": 1,
            "shard_count": 2,
            "requested_count": 1,
            "official_index_count": 1,
            "success_count": 1,
            "error_count": 0,
            "snapshots": [
                {"exchange": "SZSE", "code": "159002", "trade_date": "20260930"}
            ],
            "errors": {},
        },
    )

    result = merge_relay_snapshot(
        prepare_dir=prepare,
        shards_dir=shards,
        output_dir=output,
    )

    assert result["full_universe_count"] == 3
    assert result["pcf_found_count"] == 3
    assert result["stale_count"] == 0
    assert result["missing_count"] == 0
    assert result["coverage"]["SZSE"]["found"] == 2
    snapshot = json.loads((output / "pcf_snapshot.json").read_text(encoding="utf-8"))
    assert {(row["exchange"], row["code"]) for row in snapshot["rows"]} == {
        ("SSE", "510001"),
        ("SZSE", "159001"),
        ("SZSE", "159002"),
    }
    assert snapshot["stale"] == []
    assert snapshot["missing_reasons"] == {"SSE": {}, "SZSE": {}}
