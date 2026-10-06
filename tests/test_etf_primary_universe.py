from datetime import datetime, timezone

from runtime.etf_primary.audit_universe import audit_rows
from runtime.etf_primary.models import EtfIdentity
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
