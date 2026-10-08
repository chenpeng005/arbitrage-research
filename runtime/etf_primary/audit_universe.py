from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from typing import Iterable

from .models import EtfIdentity
from .universe import fetch_all_etf_universe


SSE_BOND_CLASSES = {"02", "32", "37"}


def _raw_classes(row: EtfIdentity) -> set[str]:
    return {item for item in (row.raw_exchange_class or "").split(",") if item}


def _problem_tags(row: EtfIdentity) -> list[str]:
    tags: list[str] = []
    raw = _raw_classes(row)

    if row.asset_class == "OTHER":
        tags.append("asset_other")
    if row.region_scope == "UNKNOWN":
        tags.append("region_unknown")
    # For money / commodity ETFs, INDEX vs ACTIVE can be structurally inapplicable
    # or absent from the exchange master. Do not treat that as a classification error.
    if row.asset_class == "EQUITY" and row.strategy_style == "UNKNOWN":
        tags.append("equity_style_unknown")

    if row.exchange == "SSE":
        if raw & SSE_BOND_CLASSES and row.asset_class != "BOND":
            tags.append("sse_bond_class_mismatch")
        if "05" in raw and row.asset_class != "MONEY_MARKET":
            tags.append("sse_money_class_mismatch")
        if "06" in raw and row.asset_class != "COMMODITY":
            tags.append("sse_commodity_class_mismatch")
        if "33" in raw and row.region_scope not in {"CROSS_BORDER", "MIXED"}:
            tags.append("sse_cross_border_class_mismatch")

    if row.qdii_flag is True and row.region_scope != "CROSS_BORDER":
        tags.append("qdii_region_mismatch")
    if row.region_scope == "MIXED" and row.qdii_flag is not None:
        tags.append("mixed_qdii_should_be_unknown")

    return tags


def audit_rows(rows: Iterable[EtfIdentity]) -> dict:
    rows = list(rows)
    keys = [(row.exchange, row.code) for row in rows]
    duplicates = sorted({key for key, count in Counter(keys).items() if count > 1})

    by_exchange = Counter(row.exchange for row in rows)
    by_region = Counter(row.region_scope for row in rows)
    by_asset = Counter(row.asset_class for row in rows)
    by_style = Counter(row.strategy_style for row in rows)
    by_qdii = Counter(str(row.qdii_flag) for row in rows)

    cross = defaultdict(Counter)
    for row in rows:
        cross[row.region_scope][row.asset_class] += 1

    problems = []
    problem_counts = Counter()
    for row in rows:
        tags = _problem_tags(row)
        if not tags:
            continue
        for tag in tags:
            problem_counts[tag] += 1
        problems.append(
            {
                "exchange": row.exchange,
                "code": row.code,
                "name": row.name,
                "tracking_index": row.tracking_index,
                "raw_exchange_class": row.raw_exchange_class,
                "region_scope": row.region_scope,
                "asset_class": row.asset_class,
                "strategy_style": row.strategy_style,
                "qdii_flag": row.qdii_flag,
                "tags": tags,
            }
        )

    return {
        "total": len(rows),
        "duplicate_keys": [list(item) for item in duplicates],
        "counts": {
            "exchange": dict(sorted(by_exchange.items())),
            "region_scope": dict(sorted(by_region.items())),
            "asset_class": dict(sorted(by_asset.items())),
            "strategy_style": dict(sorted(by_style.items())),
            "qdii_flag": dict(sorted(by_qdii.items())),
            "region_x_asset": {
                region: dict(sorted(counter.items()))
                for region, counter in sorted(cross.items())
            },
        },
        "problem_counts": dict(sorted(problem_counts.items())),
        "problem_rows": problems,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit ETF primary-market universe classification")
    parser.add_argument("--output", default="")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--szse-throttle-seconds", type=float, default=0.20)
    args = parser.parse_args(argv)

    rows = fetch_all_etf_universe(
        timeout=args.timeout,
        szse_throttle_seconds=args.szse_throttle_seconds,
    )
    report = audit_rows(rows)
    text = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
