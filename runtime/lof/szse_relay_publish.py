from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from .nav import OfficialNavRecord, fetch_szse_official_nav
from .szse_relay import RELAY_VERSION
from .universe import LofIdentity, fetch_szse_universe


def _write_json(path: Path, payload: dict) -> str:
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def build_relay_payloads(
    *,
    universe: list[LofIdentity],
    nav_rows: list[OfficialNavRecord],
    fetched_at: datetime,
) -> tuple[dict, dict]:
    if fetched_at.tzinfo is None:
        raise ValueError("fetched_at must include timezone")
    if len(universe) < 200:
        raise ValueError("SZSE official universe sanity floor failed")

    seen: set[str] = set()
    normalized_universe: list[dict] = []
    for row in sorted(universe, key=lambda item: item.code):
        if row.exchange != "SZSE":
            continue
        if row.code in seen:
            raise ValueError(f"duplicate SZSE universe code: {row.code}")
        seen.add(row.code)
        normalized_universe.append(
            {
                "code": row.code,
                "name": row.name,
                "manager": row.manager,
                "exchange": "SZSE",
                "source": "SZSE_OFFICIAL",
            }
        )

    valid_nav: list[dict] = []
    nav_seen: set[str] = set()
    for row in sorted(nav_rows, key=lambda item: item.code):
        if not row.available:
            continue
        if row.code in nav_seen:
            raise ValueError(f"duplicate SZSE NAV code: {row.code}")
        if row.code not in seen:
            continue
        nav_seen.add(row.code)
        valid_nav.append(
            {
                "code": row.code,
                "exchange": "SZSE",
                "nav": str(row.nav),
                "nav_date": row.nav_date.isoformat(),
                "source": "SZSE_OFFICIAL",
                "fetched_at": row.fetched_at.isoformat(),
            }
        )

    if len(valid_nav) < 200:
        raise ValueError("SZSE official NAV sanity floor failed")
    if len(valid_nav) / len(normalized_universe) < 0.80:
        raise ValueError("SZSE official NAV coverage below 80%")

    timestamp = fetched_at.astimezone(timezone.utc).isoformat()
    return (
        {
            "source": "SZSE_OFFICIAL",
            "fetched_at": timestamp,
            "count": len(normalized_universe),
            "rows": normalized_universe,
        },
        {
            "source": "SZSE_OFFICIAL",
            "fetched_at": timestamp,
            "count": len(valid_nav),
            "rows": valid_nav,
        },
    )


def publish_relay_snapshot(
    *,
    output_dir: Path,
    timeout: int = 20,
    nav_workers: int = 6,
) -> dict:
    fetched_at = datetime.now(timezone.utc)
    universe = fetch_szse_universe(timeout=timeout)
    nav_rows = fetch_szse_official_nav(
        [row.code for row in universe],
        timeout=timeout,
        max_workers=nav_workers,
    )
    universe_payload, nav_payload = build_relay_payloads(
        universe=universe,
        nav_rows=nav_rows,
        fetched_at=fetched_at,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    universe_name = "szse_universe.json"
    nav_name = "szse_nav.json"
    universe_hash = _write_json(
        output_dir / universe_name,
        universe_payload,
    )
    nav_hash = _write_json(
        output_dir / nav_name,
        nav_payload,
    )

    manifest = {
        "relay_version": RELAY_VERSION,
        "fetched_at": fetched_at.isoformat(),
        "source": "SZSE_OFFICIAL",
        "universe_count": len(universe_payload["rows"]),
        "nav_count": len(nav_payload["rows"]),
        "files": {
            "universe": {
                "filename": universe_name,
                "sha256": universe_hash,
            },
            "nav": {
                "filename": nav_name,
                "sha256": nav_hash,
            },
        },
    }
    manifest_hash = _write_json(
        output_dir / "manifest.json",
        manifest,
    )
    return {
        "manifest_sha256": manifest_hash,
        **manifest,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build SZSE official LOF relay snapshot."
    )
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--nav-workers", type=int, default=6)
    args = parser.parse_args(argv)

    result = publish_relay_snapshot(
        output_dir=Path(args.output_dir),
        timeout=args.timeout,
        nav_workers=args.nav_workers,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
