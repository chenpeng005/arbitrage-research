import json
from pathlib import Path

import runtime.etf_primary.pcf_shard as pcf_shard


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_collect_shard_partitions_codes_without_overlap(tmp_path, monkeypatch):
    universe_path = tmp_path / "full_universe.json"
    manifest_path = tmp_path / "prepare_manifest.json"
    index_path = tmp_path / "szse_pcf_index.json"
    codes = [f"15900{i}" for i in range(6)]
    _write(
        universe_path,
        {
            "rows": [{"exchange": "SZSE", "code": code} for code in codes]
            + [{"exchange": "SSE", "code": "510001"}]
        },
    )
    _write(manifest_path, {"target_trade_date": "20260930"})
    _write(
        index_path,
        {
            "target_trade_date": "20260930",
            "rows": [
                {
                    "code": code,
                    "xml_candidate_urls": [f"https://example.invalid/{code}.xml"],
                }
                for code in codes
            ],
        },
    )

    def fake_fetch(code: str, trade_date: str, candidate_urls: tuple[str, ...], *, timeout: int):
        assert candidate_urls
        return {"exchange": "SZSE", "code": code, "trade_date": trade_date}

    monkeypatch.setattr(pcf_shard, "_fetch_one", fake_fetch)

    first = pcf_shard.collect_shard(
        full_universe_path=universe_path,
        prepare_manifest_path=manifest_path,
        szse_pcf_index_path=index_path,
        output_path=tmp_path / "shard-0.json",
        shard_index=0,
        shard_count=2,
        workers=2,
    )
    second = pcf_shard.collect_shard(
        full_universe_path=universe_path,
        prepare_manifest_path=manifest_path,
        szse_pcf_index_path=index_path,
        output_path=tmp_path / "shard-1.json",
        shard_index=1,
        shard_count=2,
        workers=2,
    )

    first_codes = {row["code"] for row in first["snapshots"]}
    second_codes = {row["code"] for row in second["snapshots"]}
    assert first_codes.isdisjoint(second_codes)
    assert first_codes | second_codes == set(codes)
