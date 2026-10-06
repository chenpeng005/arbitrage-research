from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .audit_universe import audit_rows
from .models import EtfIdentity, PcfSnapshot
from .pcf import fetch_sse_pcf_bulk, fetch_szse_pcf_bulk, latest_pcf_trade_date
from .szse_relay import RELAY_VERSION
from .universe import fetch_sse_etf_universe, fetch_szse_etf_universe


def _write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def build_universe_payload(
    *,
    timeout: int = 20,
    throttle_seconds: float = 0.20,
) -> tuple[dict, datetime, list[EtfIdentity]]:
    fetched_at = datetime.now(timezone.utc)
    rows = fetch_szse_etf_universe(
        timeout=timeout,
        throttle_seconds=throttle_seconds,
    )
    if len(rows) < 400:
        raise ValueError(f"SZSE official ETF universe sanity floor failed: {len(rows)}")

    normalized = []
    seen: set[str] = set()
    for row in rows:
        if row.code in seen:
            raise ValueError(f"duplicate SZSE ETF code: {row.code}")
        seen.add(row.code)
        normalized.append(
            {
                "code": row.code,
                "name": row.name,
                "manager": row.manager,
                "tracking_index": row.tracking_index,
                "listing_date": row.listing_date,
                "exchange": "SZSE",
                "source": "SZSE_OFFICIAL",
            }
        )

    return (
        {
            "source": "SZSE_OFFICIAL",
            "fetched_at": fetched_at.isoformat(),
            "count": len(normalized),
            "rows": normalized,
        },
        fetched_at,
        rows,
    )


def _snapshot_row(identity: EtfIdentity, snapshot: PcfSnapshot | None) -> dict:
    base = {
        "code": identity.code,
        "name": identity.name,
        "exchange": identity.exchange,
        "manager": identity.manager,
        "tracking_index": identity.tracking_index,
        "region_scope": identity.region_scope,
        "asset_class": identity.asset_class,
        "strategy_style": identity.strategy_style,
        "qdii_flag": identity.qdii_flag,
        "pcf_available": snapshot is not None,
    }
    if snapshot is None:
        return base
    payload = snapshot.to_dict()
    payload.pop("raw_header", None)
    payload.pop("code", None)
    payload.pop("exchange", None)
    base.update(payload)
    return base


def build_pcf_snapshot(
    *,
    sse_rows: list[EtfIdentity],
    szse_rows: list[EtfIdentity],
    timeout: int,
    szse_workers: int,
    fetched_at: datetime,
) -> dict:
    sse_codes = {row.code for row in sse_rows}
    szse_codes = {row.code for row in szse_rows}

    sse_snapshots = fetch_sse_pcf_bulk(codes=sse_codes, timeout=max(timeout, 30))
    target_trade_date = latest_pcf_trade_date(sse_snapshots.values())
    if target_trade_date is None:
        raise ValueError("SSE official PCF table has no valid trading day")
    if len(sse_snapshots) < 800:
        raise ValueError(
            f"SSE PCF coverage sanity floor failed: {len(sse_snapshots)}/{len(sse_rows)}"
        )

    szse_snapshots, szse_errors = fetch_szse_pcf_bulk(
        codes=szse_codes,
        trade_date=target_trade_date,
        timeout=timeout,
        max_workers=szse_workers,
    )
    # Initial fail-closed floor. The first live audit will tell us whether the
    # official same-day list naturally excludes any legitimate ETF subtypes.
    if len(szse_snapshots) < 600:
        sample = dict(list(sorted(szse_errors.items()))[:10])
        raise ValueError(
            f"SZSE PCF coverage sanity floor failed: {len(szse_snapshots)}/{len(szse_rows)}; "
            f"sample_errors={sample}"
        )

    identities = {(row.exchange, row.code): row for row in [*sse_rows, *szse_rows]}
    snapshots = {
        **{("SSE", code): snap for code, snap in sse_snapshots.items()},
        **{("SZSE", code): snap for code, snap in szse_snapshots.items()},
    }
    rows = [
        _snapshot_row(identity, snapshots.get(key))
        for key, identity in sorted(identities.items())
    ]

    sse_latest = sum(
        1 for snapshot in sse_snapshots.values() if snapshot.trade_date == target_trade_date
    )
    szse_latest = sum(
        1 for snapshot in szse_snapshots.values() if snapshot.trade_date == target_trade_date
    )
    coverage = {
        "SSE": {
            "universe": len(sse_rows),
            "pcf_found": len(sse_snapshots),
            "latest_trade_date": sse_latest,
            "coverage_ratio": round(len(sse_snapshots) / len(sse_rows), 6),
        },
        "SZSE": {
            "universe": len(szse_rows),
            "pcf_found": len(szse_snapshots),
            "latest_trade_date": szse_latest,
            "coverage_ratio": round(len(szse_snapshots) / len(szse_rows), 6),
            "error_count": len(szse_errors),
        },
    }
    return {
        "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "target_trade_date": target_trade_date,
        "universe_count": len(identities),
        "pcf_found_count": len(snapshots),
        "coverage": coverage,
        "szse_errors": dict(sorted(szse_errors.items())),
        "rows": rows,
    }


def publish_relay_snapshot(
    *,
    output_dir: Path,
    timeout: int = 20,
    throttle_seconds: float = 0.20,
    szse_pcf_workers: int = 6,
) -> dict:
    universe, fetched_at, szse_rows = build_universe_payload(
        timeout=timeout,
        throttle_seconds=throttle_seconds,
    )
    sse_rows = fetch_sse_etf_universe(timeout=timeout)
    audit = audit_rows([*sse_rows, *szse_rows])
    if audit["duplicate_keys"]:
        raise ValueError(f"ETF universe duplicate keys found: {audit['duplicate_keys'][:5]}")

    pcf_snapshot = build_pcf_snapshot(
        sse_rows=sse_rows,
        szse_rows=szse_rows,
        timeout=timeout,
        szse_workers=szse_pcf_workers,
        fetched_at=fetched_at,
    )

    output_dir.mkdir(parents=True, exist_ok=True)

    universe_name = "szse_etf_universe.json"
    audit_name = "universe_audit.json"
    pcf_name = "pcf_snapshot.json"
    universe_hash = _write_json(output_dir / universe_name, universe)
    audit_hash = _write_json(output_dir / audit_name, audit)
    pcf_hash = _write_json(output_dir / pcf_name, pcf_snapshot)
    manifest = {
        "relay_version": RELAY_VERSION,
        "source": "SZSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "universe_count": len(universe["rows"]),
        "full_market_audit_count": audit["total"],
        "pcf_target_trade_date": pcf_snapshot["target_trade_date"],
        "pcf_found_count": pcf_snapshot["pcf_found_count"],
        "files": {
            "universe": {
                "filename": universe_name,
                "sha256": universe_hash,
            },
            "audit": {
                "filename": audit_name,
                "sha256": audit_hash,
            },
            "pcf_snapshot": {
                "filename": pcf_name,
                "sha256": pcf_hash,
            },
        },
    }
    manifest_hash = _write_json(output_dir / "manifest.json", manifest)
    return {
        "manifest_sha256": manifest_hash,
        **manifest,
        "audit_summary": audit["counts"],
        "problem_counts": audit["problem_counts"],
        "pcf_coverage": pcf_snapshot["coverage"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish official ETF primary-market relay")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--throttle-seconds", type=float, default=0.20)
    parser.add_argument("--szse-pcf-workers", type=int, default=6)
    args = parser.parse_args(argv)

    result = publish_relay_snapshot(
        output_dir=Path(args.output_dir),
        timeout=args.timeout,
        throttle_seconds=args.throttle_seconds,
        szse_pcf_workers=args.szse_pcf_workers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
