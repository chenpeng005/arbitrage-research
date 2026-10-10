from __future__ import annotations

import argparse
from dataclasses import fields
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .models import MonitorEvent, PcfSnapshot
from .monitor import detect_events
from .szse_relay import (
    DEFAULT_MAX_AGE_SECONDS,
    DEFAULT_RELAY_BASE_URL,
    fetch_etf_primary_relay_bundle,
    validate_relay_freshness,
)


SNAPSHOT_SCHEMA_VERSION = 4
EVENT_SCHEMA_VERSION = 1
_PCF_MODEL_FIELDS = {field.name for field in fields(PcfSnapshot)}
_FACT_DIGEST_EXCLUDED_FIELDS = {"fetched_at", "source_url", "raw_header"}


def _canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_json_gz(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb", compresslevel=6) as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _snapshot_from_row(row: dict[str, Any]) -> PcfSnapshot:
    payload = {key: value for key, value in row.items() if key in _PCF_MODEL_FIELDS}
    return PcfSnapshot(**payload)


def _snapshot_key(snapshot: PcfSnapshot) -> tuple[str, str]:
    return snapshot.exchange, snapshot.code


def _normalized_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    snapshots = [_snapshot_from_row(dict(row)) for row in rows]
    snapshots.sort(key=_snapshot_key)
    return [snapshot.to_dict() for snapshot in snapshots]


def _snapshot_digest(rows: list[dict[str, Any]]) -> str:
    """Hash market facts, not transport/provenance metadata.

    A historical PCF can be fetched repeatedly during a long holiday. The
    retrieval timestamp (and even an equivalent official source URL) may change
    while the PCF itself does not. Those fields must not create a fake same-day
    market refresh. Derived fields stay in the digest because a calculation or
    schema change should remain auditable.
    """
    factual_rows = [
        {
            key: value
            for key, value in row.items()
            if key not in _FACT_DIGEST_EXCLUDED_FIELDS
        }
        for row in rows
    ]
    return hashlib.sha256(_canonical_json_bytes(factual_rows)).hexdigest()


def _event_payload(
    event: MonitorEvent,
    *,
    trade_date: str,
    source: str,
    relay_fetched_at: str | None,
) -> dict[str, Any]:
    payload = event.to_dict()
    payload.update(
        {
            "trade_date": trade_date,
            "source": source,
            "relay_fetched_at": relay_fetched_at,
        }
    )
    return payload


def build_daily_diff(
    *,
    previous_rows: Iterable[dict[str, Any]] | None,
    current_rows: Iterable[dict[str, Any]],
    trade_date: str,
    source: str,
    relay_fetched_at: str | None,
    previous_trade_date: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize a daily snapshot and detect market-rule changes.

    Stale PCF rows remain in the snapshot for provenance and coverage auditing,
    but they are never eligible to create strategy events. Likewise, a stale
    previous row is treated as unavailable history rather than as a valid market
    state. Data-quality transitions must not masquerade as creation-capacity or
    creation-status transitions.
    """
    current_normalized = _normalized_rows(current_rows)
    if previous_rows is None:
        return current_normalized, []

    previous: dict[tuple[str, str], PcfSnapshot] = {}
    for row in previous_rows:
        snapshot = _snapshot_from_row(dict(row))
        if previous_trade_date is not None and snapshot.trade_date != previous_trade_date:
            continue
        previous[_snapshot_key(snapshot)] = snapshot

    events: list[dict[str, Any]] = []
    for row in current_normalized:
        current = _snapshot_from_row(row)
        if current.trade_date != trade_date:
            continue
        for event in detect_events(previous.get(_snapshot_key(current)), current):
            events.append(
                _event_payload(
                    event,
                    trade_date=trade_date,
                    source=source,
                    relay_fetched_at=relay_fetched_at,
                )
            )
    events.sort(key=lambda row: (row["severity"], row["exchange"], row["code"], row["event_type"]))
    return current_normalized, events


def _summary(
    *,
    status: str,
    trade_date: str,
    previous_trade_date: str,
    event_count: int,
    digest: str,
    snapshot_envelope: dict[str, Any],
    relay_age_seconds: int,
    archive_path: Path,
) -> dict[str, Any]:
    return {
        "status": status,
        "trade_date": trade_date,
        "previous_trade_date": previous_trade_date or None,
        "event_count": event_count,
        "snapshot_sha256": digest,
        "pcf_found_count": snapshot_envelope["pcf_found_count"],
        "stale_count": snapshot_envelope["stale_count"],
        "missing_count": sum(
            len(value) for value in (snapshot_envelope["missing"] or {}).values()
            if isinstance(value, list)
        ),
        "relay_age_seconds": relay_age_seconds,
        "archive_path": str(archive_path),
    }


def run_cycle(
    *,
    state_dir: Path,
    relay_base_url: str = DEFAULT_RELAY_BASE_URL,
    timeout: int = 20,
    max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS,
    now: datetime | None = None,
) -> dict[str, Any]:
    observed_at = now or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)

    bundle = fetch_etf_primary_relay_bundle(relay_base_url, timeout=timeout)
    relay_age_seconds = validate_relay_freshness(
        bundle,
        as_of=observed_at,
        max_age_seconds=max_age_seconds,
    )
    if bundle.pcf_snapshot is None:
        raise ValueError("combined ETF primary relay has no PCF snapshot")

    relay_snapshot = bundle.pcf_snapshot
    trade_date = str(relay_snapshot.get("target_trade_date") or bundle.target_trade_date or "")
    if not trade_date:
        raise ValueError("ETF primary relay PCF snapshot has no target trade date")
    rows = relay_snapshot.get("rows") or []
    if not isinstance(rows, list) or len(rows) < 1000:
        raise ValueError(f"ETF primary relay PCF snapshot row count is implausible: {len(rows) if isinstance(rows, list) else 'invalid'}")

    current_pointer_path = state_dir / "current.json"
    previous_payload: dict[str, Any] | None = None
    if current_pointer_path.exists():
        loaded = _read_json(current_pointer_path)
        if isinstance(loaded, dict):
            previous_payload = loaded

    previous_trade_date = str((previous_payload or {}).get("trade_date") or "")
    if previous_trade_date and trade_date < previous_trade_date:
        raise ValueError(
            f"relay trade date moved backwards: {trade_date} < {previous_trade_date}"
        )

    source = str(relay_snapshot.get("source") or bundle.manifest.get("source") or "")
    relay_fetched_at = str(bundle.manifest.get("fetched_at") or "") or None
    baseline = previous_payload is None
    same_trade_date = previous_trade_date == trade_date and previous_payload is not None

    # Normalize and hash only market facts. Fetch/provenance metadata is retained
    # in the archived rows, but cannot create a same-day market refresh.
    current_rows = _normalized_rows(rows)
    digest = _snapshot_digest(current_rows)
    previous_candidate_rows = (previous_payload or {}).get("rows")
    previous_fact_digest = (
        _snapshot_digest(previous_candidate_rows)
        if isinstance(previous_candidate_rows, list)
        else str((previous_payload or {}).get("snapshot_sha256") or "")
    )
    previous_schema_version = int((previous_payload or {}).get("schema_version") or 0)
    same_market_facts = same_trade_date and previous_fact_digest == digest
    archive_path = state_dir / "snapshots" / f"{trade_date}.json.gz"

    if same_market_facts and previous_schema_version >= SNAPSHOT_SCHEMA_VERSION:
        return _summary(
            status="SAME_TRADE_DATE_NO_CHANGE",
            trade_date=trade_date,
            previous_trade_date=previous_trade_date,
            event_count=0,
            digest=digest,
            snapshot_envelope=previous_payload,
            relay_age_seconds=relay_age_seconds,
            archive_path=archive_path,
        )

    previous_rows: list[dict[str, Any]] | None = None
    events: list[dict[str, Any]] = []
    if previous_payload is not None and not same_trade_date:
        candidate = previous_payload.get("rows")
        if isinstance(candidate, list):
            previous_rows = candidate
        current_rows, events = build_daily_diff(
            previous_rows=previous_rows,
            current_rows=current_rows,
            trade_date=trade_date,
            previous_trade_date=previous_trade_date,
            source=source,
            relay_fetched_at=relay_fetched_at,
        )
        digest = _snapshot_digest(current_rows)

    snapshot_envelope = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "trade_date": trade_date,
        "source": source,
        "relay_fetched_at": relay_fetched_at,
        "relay_age_seconds": relay_age_seconds,
        "observed_at": observed_at.astimezone(timezone.utc).isoformat(),
        "snapshot_sha256": digest,
        "universe_count": int(relay_snapshot.get("universe_count") or len(current_rows)),
        "pcf_found_count": int(relay_snapshot.get("pcf_found_count") or len(current_rows)),
        "latest_trade_date_count": int(relay_snapshot.get("latest_trade_date_count") or 0),
        "stale_count": int(relay_snapshot.get("stale_count") or 0),
        "coverage": relay_snapshot.get("coverage") or {},
        "missing": relay_snapshot.get("missing") or {},
        "stale": relay_snapshot.get("stale") or [],
        "rows": current_rows,
    }

    state_dir.mkdir(parents=True, exist_ok=True)
    _write_json_gz(archive_path, snapshot_envelope)

    status: str
    if baseline:
        status = "BASELINE_CREATED"
        events = []
    elif same_trade_date and same_market_facts and previous_schema_version < SNAPSHOT_SCHEMA_VERSION:
        status = "SAME_TRADE_DATE_SCHEMA_MIGRATED"
        events = []
    elif same_trade_date:
        status = "SAME_TRADE_DATE_REFRESHED"
        # A same-day market-fact correction replaces the stored snapshot but
        # never fabricates a cross-day market event.
        events = []
    else:
        status = "DIFF_COMPLETED"
        event_envelope = {
            "schema_version": EVENT_SCHEMA_VERSION,
            "trade_date": trade_date,
            "previous_trade_date": previous_trade_date,
            "source": source,
            "generated_at": observed_at.astimezone(timezone.utc).isoformat(),
            "event_count": len(events),
            "events": events,
        }
        _write_json(state_dir / "events" / f"{trade_date}.json", event_envelope)

    # Keep one uncompressed pointer for the next diff. This avoids scanning the
    # archive while historical snapshots remain compressed and cheap to retain.
    _write_json(current_pointer_path, snapshot_envelope)
    summary = _summary(
        status=status,
        trade_date=trade_date,
        previous_trade_date=previous_trade_date,
        event_count=len(events),
        digest=digest,
        snapshot_envelope=snapshot_envelope,
        relay_age_seconds=relay_age_seconds,
        archive_path=archive_path,
    )
    _write_json(state_dir / "last_run.json", summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one ETF primary-market daily snapshot/diff cycle")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--relay-base-url", default=DEFAULT_RELAY_BASE_URL)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-age-seconds", type=int, default=DEFAULT_MAX_AGE_SECONDS)
    args = parser.parse_args(argv)

    result = run_cycle(
        state_dir=Path(args.state_dir),
        relay_base_url=args.relay_base_url,
        timeout=args.timeout,
        max_age_seconds=args.max_age_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
