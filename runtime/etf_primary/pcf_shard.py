from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import HTTPError

from .http import fetch_text
from .models import PcfSnapshot
from .pcf import SZSE_PCF_PAGE, parse_szse_xml


def _compact_snapshot(snapshot: PcfSnapshot) -> dict:
    row = snapshot.to_dict()
    row.pop("raw_header", None)
    return row


def _fetch_one(
    code: str,
    trade_date: str,
    candidate_urls: tuple[str, ...],
    *,
    timeout: int,
) -> dict:
    if not candidate_urls:
        raise RuntimeError("not_in_official_day_index")
    last_error: Exception | None = None
    for index, url in enumerate(candidate_urls):
        try:
            text = fetch_text(
                url,
                referer=SZSE_PCF_PAGE,
                timeout=timeout,
                attempts=1,
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
            if snapshot.trade_date and snapshot.trade_date != trade_date:
                raise ValueError(
                    f"PCF trading day mismatch: {snapshot.trade_date} != {trade_date}"
                )
            return _compact_snapshot(snapshot)
        except HTTPError as exc:
            last_error = exc
            # 403 is a rate-limit/WAF signal. Trying another filename on the same
            # host only makes it worse, so fail this shard row immediately.
            if exc.code == 403:
                break
            if exc.code != 404 or index + 1 >= len(candidate_urls):
                break
        except Exception as exc:
            last_error = exc
            if index + 1 >= len(candidate_urls):
                break
    raise RuntimeError(f"{type(last_error).__name__}: {last_error}")


def collect_shard(
    *,
    full_universe_path: Path,
    prepare_manifest_path: Path,
    szse_pcf_index_path: Path,
    output_path: Path,
    shard_index: int,
    shard_count: int,
    timeout: int = 20,
    workers: int = 6,
) -> dict:
    universe = json.loads(full_universe_path.read_text(encoding="utf-8"))
    manifest = json.loads(prepare_manifest_path.read_text(encoding="utf-8"))
    pcf_index_payload = json.loads(szse_pcf_index_path.read_text(encoding="utf-8"))
    trade_date = str(manifest["target_trade_date"])
    if str(pcf_index_payload.get("target_trade_date") or "") != trade_date:
        raise ValueError("SZSE PCF index trade date does not match prepare manifest")
    pcf_index = {
        str(row.get("code") or ""): tuple(str(url) for url in row.get("xml_candidate_urls") or [])
        for row in pcf_index_payload.get("rows") or []
        if str(row.get("code") or "")
    }

    codes = sorted(
        str(row["code"])
        for row in universe.get("rows") or []
        if str(row.get("exchange") or "") == "SZSE"
    )
    selected = [
        code for position, code in enumerate(codes)
        if position % shard_count == shard_index
    ]
    snapshots: dict[str, dict] = {}
    errors: dict[str, str] = {}

    available = {code: pcf_index[code] for code in selected if code in pcf_index}
    for code in sorted(set(selected) - set(available)):
        errors[code] = "not_in_official_day_index"

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures = {
            pool.submit(
                _fetch_one,
                code,
                trade_date,
                candidate_urls,
                timeout=timeout,
            ): code
            for code, candidate_urls in available.items()
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                snapshots[code] = future.result()
            except Exception as exc:
                errors[code] = f"{type(exc).__name__}: {exc}"

    payload = {
        "source": "SZSE_OFFICIAL",
        "trade_date": trade_date,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "requested_count": len(selected),
        "official_index_count": len(available),
        "success_count": len(snapshots),
        "error_count": len(errors),
        "snapshots": [snapshots[code] for code in sorted(snapshots)],
        "errors": dict(sorted(errors.items())),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch one SZSE official PCF shard")
    parser.add_argument("--full-universe", required=True)
    parser.add_argument("--prepare-manifest", required=True)
    parser.add_argument("--szse-pcf-index", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-count", type=int, required=True)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args(argv)
    if args.shard_count <= 0 or not 0 <= args.shard_index < args.shard_count:
        raise SystemExit("invalid shard index/count")

    result = collect_shard(
        full_universe_path=Path(args.full_universe),
        prepare_manifest_path=Path(args.prepare_manifest),
        szse_pcf_index_path=Path(args.szse_pcf_index),
        output_path=Path(args.output),
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        timeout=args.timeout,
        workers=args.workers,
    )
    print(json.dumps({k: v for k, v in result.items() if k not in {"snapshots", "errors"}}, ensure_ascii=False))
    # A shard with widespread failure is not safe to aggregate. A very small
    # number of funds can legitimately be absent from the day's official index.
    if result["success_count"] < int(result["requested_count"] * 0.90):
        sample = dict(list(result["errors"].items())[:10])
        raise SystemExit(f"SZSE PCF shard coverage too low: {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
