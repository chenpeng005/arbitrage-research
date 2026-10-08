from datetime import datetime, timezone
import hashlib
import json

from runtime.etf_primary.audit_universe import audit_rows
from runtime.etf_primary.models import EtfIdentity
import runtime.etf_primary.szse_relay as relay
from runtime.etf_primary.szse_relay import SzseEtfRelayBundle, validate_relay_freshness


def test_audit_flags_exchange_class_mismatch():
    rows = [
        EtfIdentity(
            code="511010",
            name="国债ETF",
            exchange="SSE",
            region_scope="DOMESTIC",
            asset_class="EQUITY",
            strategy_style="INDEX",
            qdii_flag=False,
            raw_exchange_class="02",
        )
    ]
    report = audit_rows(rows)
    assert report["problem_counts"]["sse_bond_class_mismatch"] == 1


def test_audit_accepts_clean_cross_border_equity():
    rows = [
        EtfIdentity(
            code="513100",
            name="纳指ETF国泰",
            exchange="SSE",
            region_scope="CROSS_BORDER",
            asset_class="EQUITY",
            strategy_style="INDEX",
            qdii_flag=True,
            raw_exchange_class="33",
        )
    ]
    report = audit_rows(rows)
    assert report["duplicate_keys"] == []
    assert report["problem_counts"] == {}


def test_relay_freshness_contract():
    bundle = SzseEtfRelayBundle(
        base_url="https://example.invalid/relay",
        manifest={"fetched_at": "2026-10-06T00:00:00+00:00"},
        universe={"rows": []},
        manifest_sha256="x",
    )
    age = validate_relay_freshness(
        bundle,
        as_of=datetime(2026, 10, 6, 1, 0, tzinfo=timezone.utc),
        max_age_seconds=7200,
    )
    assert age == 3600


def test_combined_relay_v2_loader(monkeypatch):
    universe = {"rows": [{"code": str(159000 + i)} for i in range(400)]}
    full_universe = {
        "rows": [{"exchange": "SZSE", "code": str(159000 + i)} for i in range(400)]
    }
    pcf_snapshot = {
        "target_trade_date": "20260930",
        "pcf_found_count": 400,
        "rows": [],
    }

    files = {
        "szse_etf_universe.json": json.dumps(universe).encode(),
        "full_universe.json": json.dumps(full_universe).encode(),
        "pcf_snapshot.json": json.dumps(pcf_snapshot).encode(),
    }
    manifest = {
        "relay_version": relay.RELAY_VERSION,
        "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
        "fetched_at": "2026-10-06T00:00:00+00:00",
        "target_trade_date": "20260930",
        "full_universe_count": 400,
        "pcf_found_count": 400,
        "files": {
            name: {"sha256": hashlib.sha256(raw).hexdigest()}
            for name, raw in files.items()
        },
    }
    files["manifest.json"] = json.dumps(manifest).encode()

    def fake_fetch(base_url, filename, *, timeout, pinned_ref=None):
        return files[filename]

    monkeypatch.setattr(relay, "_fetch_relay_file", fake_fetch)
    bundle = relay.fetch_etf_primary_relay_bundle(
        "https://example.invalid/relay",
        timeout=1,
    )
    assert len(bundle.universe["rows"]) == 400
    assert bundle.full_universe is not None
    assert bundle.pcf_snapshot is not None
    assert bundle.target_trade_date == "20260930"
