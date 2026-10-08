from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .audit_universe import audit_rows
from .models import PcfSnapshot
from .pcf import fetch_sse_pcf_bulk, fetch_szse_pcf_day_index, latest_pcf_trade_date
from .universe import fetch_sse_etf_universe, fetch_szse_etf_universe


def _write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _compact_snapshot(snapshot: PcfSnapshot) -> dict:
    row = snapshot.to_dict()
    row.pop("raw_header", None)
    return row


def prepare_snapshot(
    *,
    output_dir: Path,
    timeout: int = 20,
    szse_throttle_seconds: float = 0.20,
) -> dict:
    fetched_at = datetime.now(timezone.utc)
    sse_rows = fetch_sse_etf_universe(timeout=timeout)
    szse_rows = fetch_szse_etf_universe(
        timeout=timeout,
        throttle_seconds=szse_throttle_seconds,
    )
    if len(sse_rows) < 800:
        raise ValueError(f"SSE ETF universe sanity floor failed: {len(sse_rows)}")
    if len(szse_rows) < 400:
        raise ValueError(f"SZSE ETF universe sanity floor failed: {len(szse_rows)}")

    all_rows = [*sse_rows, *szse_rows]
    audit = audit_rows(all_rows)
    if audit["duplicate_keys"]:
        raise ValueError(f"ETF universe duplicate keys found: {audit['duplicate_keys'][:5]}")

    sse_snapshots = fetch_sse_pcf_bulk(
        codes={row.code for row in sse_rows},
        timeout=max(timeout, 30),
    )
    if len(sse_snapshots) < 800:
        raise ValueError(
            f"SSE PCF coverage sanity floor failed: {len(sse_snapshots)}/{len(sse_rows)}"
        )
    target_trade_date = latest_pcf_trade_date(sse_snapshots.values())
    if target_trade_date is None:
        raise ValueError("SSE PCF table has no valid trading day")

    # Persist the official SZSE day index once. Besides confirming which funds
    # actually published a PCF for the target day, it carries the exact official
    # download filenames. A few funds do not follow the generic pcf_CODE_DATE
    # naming convention, so shard workers must consume this index rather than
    # guess URLs independently.
    szse_day_index = fetch_szse_pcf_day_index(
        target_trade_date,
        timeout=max(timeout, 30),
    )
    if len(szse_day_index) < 700:
        raise ValueError(
            f"SZSE PCF day-index sanity floor failed: {len(szse_day_index)}/{len(szse_rows)}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    szse_universe = {
        "source": "SZSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "count": len(szse_rows),
        "rows": [
            {
                "code": row.code,
                "name": row.name,
                "manager": row.manager,
                "tracking_index": row.tracking_index,
                "listing_date": row.listing_date,
                "exchange": "SZSE",
                "source": "SZSE_OFFICIAL",
            }
            for row in szse_rows
        ],
    }
    full_universe = {
        "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "count": len(all_rows),
        "rows": [row.to_dict() for row in all_rows],
    }
    sse_pcf = {
        "source": "SSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "target_trade_date": target_trade_date,
        "universe_count": len(sse_rows),
        "pcf_found_count": len(sse_snapshots),
        "latest_trade_date_count": sum(
            1 for row in sse_snapshots.values() if row.trade_date == target_trade_date
        ),
        "rows": [
            _compact_snapshot(sse_snapshots[code])
            for code in sorted(sse_snapshots)
        ],
    }
    szse_pcf_index = {
        "source": "SZSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "target_trade_date": target_trade_date,
        "count": len(szse_day_index),
        "rows": [
            {
                "code": entry.code,
                "trade_date": entry.trade_date,
                "page_label": entry.page_label,
                "source_page_url": entry.source_page_url,
                "xml_candidate_urls": list(entry.xml_candidate_urls),
            }
            for entry in sorted(szse_day_index.values(), key=lambda item: item.code)
        ],
    }

    hashes = {
        "szse_universe": _write_json(output_dir / "szse_etf_universe.json", szse_universe),
        "full_universe": _write_json(output_dir / "full_universe.json", full_universe),
        "audit": _write_json(output_dir / "universe_audit.json", audit),
        "sse_pcf": _write_json(output_dir / "sse_pcf.json", sse_pcf),
        "szse_pcf_index": _write_json(output_dir / "szse_pcf_index.json", szse_pcf_index),
    }
    manifest = {
        "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "full_universe_count": len(all_rows),
        "sse_count": len(sse_rows),
        "szse_count": len(szse_rows),
        "szse_pcf_index_count": len(szse_day_index),
        "target_trade_date": target_trade_date,
        "hashes": hashes,
        "audit_counts": audit["counts"],
        "problem_counts": audit["problem_counts"],
    }
    _write_json(output_dir / "prepare_manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare official ETF primary-market data")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--szse-throttle-seconds", type=float, default=0.20)
    args = parser.parse_args(argv)

    result = prepare_snapshot(
        output_dir=Path(args.output_dir),
        timeout=args.timeout,
        szse_throttle_seconds=args.szse_throttle_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
