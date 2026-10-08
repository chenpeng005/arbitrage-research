from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

from .http import fetch_text
from .monitor_view import build_monitor_view
from .pcf import SZSE_PCF_PAGE, parse_szse_xml


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> str:
    raw = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _compact_snapshot(snapshot) -> dict:
    row = snapshot.to_dict()
    row.pop("raw_header", None)
    return row


def _recover_sparse_szse_misses(
    *,
    missing_codes: list[str],
    index_payload: dict,
    target_trade_date: str,
    timeout: int = 20,
    maximum_retries: int = 10,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Retry only a small number of shard misses from a fresh runner IP.

    The normal path is the sharded collector. This recovery pass exists for
    sparse CDN/WAF/edge failures and is deliberately capped so merge cannot
    become another full-market crawler.
    """
    if not missing_codes or len(missing_codes) > maximum_retries:
        return {}, {}

    index = {
        str(row.get("code") or ""): tuple(str(url) for url in row.get("xml_candidate_urls") or [])
        for row in index_payload.get("rows") or []
        if str(row.get("code") or "")
    }
    recovered: dict[str, dict] = {}
    errors: dict[str, str] = {}
    for code in missing_codes:
        urls = index.get(code) or ()
        if not urls:
            errors[code] = "not_in_official_day_index"
            continue
        last_error: Exception | None = None
        for position, url in enumerate(urls):
            try:
                text = fetch_text(
                    url,
                    referer=SZSE_PCF_PAGE,
                    timeout=timeout,
                    attempts=2,
                    base_delay_seconds=0.5,
                )
                stripped = text.lstrip()
                if not stripped.startswith("<") or "html" in stripped[:160].lower():
                    raise ValueError("official PCF endpoint returned HTML instead of XML")
                snapshot = parse_szse_xml(
                    code,
                    text,
                    source_url=url,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                )
                if snapshot.trade_date and snapshot.trade_date != target_trade_date:
                    raise ValueError(
                        f"PCF trading day mismatch: {snapshot.trade_date} != {target_trade_date}"
                    )
                recovered[code] = _compact_snapshot(snapshot)
                break
            except HTTPError as exc:
                last_error = exc
                if exc.code == 403:
                    break
                if exc.code != 404 or position + 1 >= len(urls):
                    break
            except Exception as exc:
                last_error = exc
                if position + 1 >= len(urls):
                    break
        if code not in recovered:
            errors[code] = f"{type(last_error).__name__}: {last_error}"
    return recovered, errors


def merge_relay_snapshot(
    *,
    prepare_dir: Path,
    shards_dir: Path,
    output_dir: Path,
    minimum_szse_coverage: float = 0.97,
) -> dict:
    prepare_manifest = _read_json(prepare_dir / "prepare_manifest.json")
    full_universe = _read_json(prepare_dir / "full_universe.json")
    sse_pcf = _read_json(prepare_dir / "sse_pcf.json")
    szse_pcf_index = _read_json(prepare_dir / "szse_pcf_index.json")
    target_trade_date = str(prepare_manifest["target_trade_date"])

    expected_szse = sorted(
        str(row["code"])
        for row in full_universe.get("rows") or []
        if str(row.get("exchange") or "") == "SZSE"
    )
    expected_sse = sorted(
        str(row["code"])
        for row in full_universe.get("rows") or []
        if str(row.get("exchange") or "") == "SSE"
    )

    shard_files = sorted(shards_dir.rglob("shard-*.json"))
    if not shard_files:
        raise ValueError("no SZSE PCF shard files found")

    szse_rows: dict[str, dict] = {}
    shard_errors: dict[str, str] = {}
    shard_summaries: list[dict] = []
    seen_shards: set[int] = set()
    shard_count: int | None = None

    for path in shard_files:
        payload = _read_json(path)
        if str(payload.get("trade_date")) != target_trade_date:
            raise ValueError(
                f"shard trade date mismatch: {path.name}: "
                f"{payload.get('trade_date')} != {target_trade_date}"
            )
        current_count = int(payload.get("shard_count") or 0)
        current_index = int(payload.get("shard_index") or 0)
        if shard_count is None:
            shard_count = current_count
        elif current_count != shard_count:
            raise ValueError("inconsistent shard_count across SZSE PCF shards")
        if current_index in seen_shards:
            raise ValueError(f"duplicate SZSE PCF shard index: {current_index}")
        seen_shards.add(current_index)

        for row in payload.get("snapshots") or []:
            code = str(row.get("code") or "")
            if not code:
                continue
            if code in szse_rows:
                raise ValueError(f"duplicate SZSE PCF code across shards: {code}")
            szse_rows[code] = row
        for code, message in (payload.get("errors") or {}).items():
            shard_errors[str(code)] = str(message)
        shard_summaries.append(
            {
                "shard_index": current_index,
                "requested_count": int(payload.get("requested_count") or 0),
                "official_index_count": int(payload.get("official_index_count") or 0),
                "success_count": int(payload.get("success_count") or 0),
                "error_count": int(payload.get("error_count") or 0),
            }
        )

    if shard_count is None or seen_shards != set(range(shard_count)):
        missing_shards = sorted(set(range(shard_count or 0)) - seen_shards)
        raise ValueError(f"incomplete SZSE PCF shard set: missing={missing_shards}")

    unexpected_szse = sorted(set(szse_rows) - set(expected_szse))
    if unexpected_szse:
        raise ValueError(f"unexpected SZSE PCF codes: {unexpected_szse[:10]}")

    pre_recovery_missing = sorted(set(expected_szse) - set(szse_rows))
    recovered_rows, recovery_errors = _recover_sparse_szse_misses(
        missing_codes=pre_recovery_missing,
        index_payload=szse_pcf_index,
        target_trade_date=target_trade_date,
        timeout=20,
    )
    szse_rows.update(recovered_rows)
    for code, message in recovery_errors.items():
        shard_errors[code] = f"retry_failed: {message}; shard_error={shard_errors.get(code, 'missing')}"

    missing_szse = sorted(set(expected_szse) - set(szse_rows))
    szse_coverage = len(szse_rows) / len(expected_szse) if expected_szse else 1.0
    if szse_coverage < minimum_szse_coverage:
        sample = {code: shard_errors.get(code, "missing") for code in missing_szse[:10]}
        raise ValueError(
            f"SZSE PCF aggregate coverage too low: {len(szse_rows)}/{len(expected_szse)}; "
            f"sample={sample}"
        )

    sse_rows = {
        str(row.get("code") or ""): row
        for row in sse_pcf.get("rows") or []
        if str(row.get("code") or "")
    }
    missing_sse = sorted(set(expected_sse) - set(sse_rows))

    combined_rows = [sse_rows[code] for code in sorted(sse_rows)] + [
        szse_rows[code] for code in sorted(szse_rows)
    ]
    stale_rows = sorted(
        (
            {
                "exchange": str(row.get("exchange") or ""),
                "code": str(row.get("code") or ""),
                "trade_date": str(row.get("trade_date") or ""),
            }
            for row in combined_rows
            if str(row.get("trade_date") or "") != target_trade_date
        ),
        key=lambda row: (row["exchange"], row["code"]),
    )
    latest_trade_date_count = len(combined_rows) - len(stale_rows)

    output_dir.mkdir(parents=True, exist_ok=True)
    prepared_files = (
        "szse_etf_universe.json",
        "full_universe.json",
        "universe_audit.json",
        "sse_pcf.json",
        "szse_pcf_index.json",
    )
    for filename in prepared_files:
        shutil.copy2(prepare_dir / filename, output_dir / filename)

    pcf_snapshot = {
        "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "target_trade_date": target_trade_date,
        "universe_count": len(expected_sse) + len(expected_szse),
        "pcf_found_count": len(combined_rows),
        "latest_trade_date_count": latest_trade_date_count,
        "stale_count": len(stale_rows),
        "recovered_szse_count": len(recovered_rows),
        "coverage": {
            "SSE": {
                "universe": len(expected_sse),
                "found": len(sse_rows),
                "missing": len(missing_sse),
            },
            "SZSE": {
                "universe": len(expected_szse),
                "found": len(szse_rows),
                "missing": len(missing_szse),
                "ratio": round(szse_coverage, 6),
            },
        },
        "missing": {
            "SSE": missing_sse,
            "SZSE": missing_szse,
        },
        "missing_reasons": {
            "SSE": {code: "missing_from_official_bulk_table" for code in missing_sse},
            "SZSE": {code: shard_errors.get(code, "missing") for code in missing_szse},
        },
        "stale": stale_rows,
        "rows": combined_rows,
    }
    pcf_hash = _write_json(output_dir / "pcf_snapshot.json", pcf_snapshot)
    monitor_view = build_monitor_view(
        full_universe=full_universe,
        pcf_snapshot=pcf_snapshot,
    )
    monitor_view_hash = _write_json(output_dir / "monitor_view.json", monitor_view)

    files: dict[str, dict[str, str]] = {}
    for filename in prepared_files:
        raw = (output_dir / filename).read_bytes()
        files[filename] = {"sha256": hashlib.sha256(raw).hexdigest()}
    files["pcf_snapshot.json"] = {"sha256": pcf_hash}
    files["monitor_view.json"] = {"sha256": monitor_view_hash}

    manifest = {
        "relay_version": "etf-primary-official-relay-v2",
        "source": "SSE_OFFICIAL+SZSE_OFFICIAL",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "target_trade_date": target_trade_date,
        "full_universe_count": len(expected_sse) + len(expected_szse),
        "pcf_found_count": len(combined_rows),
        "latest_trade_date_count": latest_trade_date_count,
        "stale_count": len(stale_rows),
        "missing_count": len(missing_sse) + len(missing_szse),
        "recovered_szse_count": len(recovered_rows),
        "monitor_view": {
            "universe_count": monitor_view["universe_count"],
            "fresh_count": monitor_view["fresh_count"],
            "stale_count": monitor_view["stale_count"],
            "missing_count": monitor_view["missing_count"],
        },
        "coverage": pcf_snapshot["coverage"],
        "shards": sorted(shard_summaries, key=lambda row: row["shard_index"]),
        "files": files,
    }
    _write_json(output_dir / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge official ETF PCF relay shards")
    parser.add_argument("--prepare-dir", required=True)
    parser.add_argument("--shards-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--minimum-szse-coverage", type=float, default=0.97)
    args = parser.parse_args(argv)

    result = merge_relay_snapshot(
        prepare_dir=Path(args.prepare_dir),
        shards_dir=Path(args.shards_dir),
        output_dir=Path(args.output_dir),
        minimum_szse_coverage=args.minimum_szse_coverage,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
