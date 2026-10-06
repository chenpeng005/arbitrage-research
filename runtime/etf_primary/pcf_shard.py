from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path
from urllib.error import HTTPError

from .http import fetch_text
from .models import PcfSnapshot
from .pcf import SZSE_PCF_PAGE, SZSE_REPORTDOCS_BASE, parse_szse_xml


def _compact_snapshot(snapshot: PcfSnapshot) -> dict:
    row = snapshot.to_dict()
    row.pop("raw_header", None)
    return row


def _fetch_one(code: str, trade_date: str, *, timeout: int) -> dict:
    candidates = (
        f"{SZSE_REPORTDOCS_BASE}/files/text/ETFDown/pcf_{code}_{trade_date}.xml",
        f"{SZSE_REPORTDOCS_BASE}/files/text/ETFDown/{code}ETF{trade_date}.xml",
    )
    last_error: Exception | None = None
    for index, url in enumerate(candidates):
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
            snapshot = parse_szse_xml(code, text, source_url=url)
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
            if exc.code != 404 or index + 1 >= len(candidates):
                break
        except Exception as exc:
            last_error = exc
            if index + 1 >= len(candidates):
                break
    raise RuntimeError(f"{type(last_error).__name__}: {last_error}")


def collect_shard(
    *,
    full_universe_path: Path,
    prepare_manifest_path: Path,
    output_path: Path,
    shard_index: int,
    shard_count: int,
    timeout: int = 20,
    workers: int = 6,
) -> dict:
    universe = json.loads(full_universe_path.read_text(encoding="utf-8"))
    manifest = json.loads(prepare_manifest_path.read_text(encoding="utf-8"))
    trade_date = str(manifest["target_trade_date"])
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

    with ThreadPoolExecutor(max_workers=max(1, min(workers, 8))) as pool:
        futures = {
            pool.submit(_fetch_one, code, trade_date, timeout=timeout): code
            for code in selected
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
        output_path=Path(args.output),
        shard_index=args.shard_index,
        shard_count=args.shard_count,
        timeout=args.timeout,
        workers=args.workers,
    )
    print(json.dumps({k: v for k, v in result.items() if k not in {"snapshots", "errors"}}, ensure_ascii=False))
    # A shard with widespread failure is not safe to aggregate.
    if result["success_count"] < int(result["requested_count"] * 0.90):
        sample = dict(list(result["errors"].items())[:10])
        raise SystemExit(f"SZSE PCF shard coverage too low: {sample}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
