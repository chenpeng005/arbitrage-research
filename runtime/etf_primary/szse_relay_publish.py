from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .szse_relay import RELAY_VERSION
from .universe import fetch_szse_etf_universe


def _write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def build_universe_payload(*, timeout: int = 20, throttle_seconds: float = 0.20) -> tuple[dict, datetime]:
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
    )


def publish_relay_snapshot(
    *,
    output_dir: Path,
    timeout: int = 20,
    throttle_seconds: float = 0.20,
) -> dict:
    universe, fetched_at = build_universe_payload(
        timeout=timeout,
        throttle_seconds=throttle_seconds,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    universe_name = "szse_etf_universe.json"
    universe_hash = _write_json(output_dir / universe_name, universe)
    manifest = {
        "relay_version": RELAY_VERSION,
        "source": "SZSE_OFFICIAL",
        "fetched_at": fetched_at.isoformat(),
        "universe_count": len(universe["rows"]),
        "files": {
            "universe": {
                "filename": universe_name,
                "sha256": universe_hash,
            }
        },
    }
    manifest_hash = _write_json(output_dir / "manifest.json", manifest)
    return {"manifest_sha256": manifest_hash, **manifest}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish official SZSE ETF universe relay")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--throttle-seconds", type=float, default=0.20)
    args = parser.parse_args(argv)

    result = publish_relay_snapshot(
        output_dir=Path(args.output_dir),
        timeout=args.timeout,
        throttle_seconds=args.throttle_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
