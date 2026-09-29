from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .runtime_session import LofRuntimeSession
from .snapshot_store import LofSnapshotStore


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def run_snapshot_once(
    *,
    data_root: str | Path,
    as_of: datetime | None = None,
    timeout: int = 15,
    max_quote_age_seconds: int = 60,
    snapshot_id: str | None = None,
    sse_universe_fixture_path: str | Path | None = None,
) -> tuple[dict, Path, LofRuntimeSession]:
    now = as_of or datetime.now(SHANGHAI_TZ)
    if now.tzinfo is None:
        now = now.replace(tzinfo=SHANGHAI_TZ)

    session = LofRuntimeSession.build(
        as_of=now,
        timeout=timeout,
        sse_universe_fixture_path=sse_universe_fixture_path,
    )
    snapshot = session.collect(
        generated_at=now,
        market_cutoff=now,
        max_quote_age_seconds=max_quote_age_seconds,
        timeout=timeout,
        snapshot_id=snapshot_id,
    )
    store = LofSnapshotStore(data_root)
    path = store.persist(snapshot)
    return snapshot, path, session


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate and persist one all-market LOF snapshot."
    )
    parser.add_argument(
        "--data-root",
        required=True,
        help="Runtime data directory for LOF snapshots.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
    )
    parser.add_argument(
        "--max-quote-age-seconds",
        type=int,
        default=60,
    )
    parser.add_argument(
        "--snapshot-id",
        default=None,
    )
    parser.add_argument(
        "--sse-universe-fixture",
        default=None,
        help=(
            "Optional SSE official-universe fixture for cloud smoke only. "
            "Production should omit this argument."
        ),
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    snapshot, path, session = run_snapshot_once(
        data_root=args.data_root,
        timeout=args.timeout,
        max_quote_age_seconds=args.max_quote_age_seconds,
        snapshot_id=args.snapshot_id,
        sse_universe_fixture_path=args.sse_universe_fixture,
    )

    quality = snapshot.get("quality_summary") or {}
    print(f"snapshot_id={snapshot.get('snapshot_id')}")
    print(f"collector_status={snapshot.get('collector_status')}")
    print(f"universe_count={snapshot.get('universe_count')}")
    print(
        "estimated_nav_available_count="
        f"{quality.get('estimated_nav_available_count')}"
    )
    print(
        "r1_context_resolved="
        f"{session.context_build.r1_resolved_count}"
    )
    print(
        "r1_context_unresolved="
        f"{session.context_build.r1_unresolved_count}"
    )
    print(f"snapshot_path={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
