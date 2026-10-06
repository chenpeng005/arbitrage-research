from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


VIEW_SCHEMA_VERSION = 1


def _read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("exchange") or ""), str(row.get("code") or "")


def _capacity_kind(pcf: dict[str, Any]) -> str | None:
    creation_limit = pcf.get("creation_limit")
    if isinstance(creation_limit, (int, float)) and creation_limit > 0:
        return "CUMULATIVE"
    net_creation_limit = pcf.get("net_creation_limit")
    if isinstance(net_creation_limit, (int, float)) and net_creation_limit > 0:
        return "NET"
    return None


def build_monitor_view(
    *,
    full_universe: dict[str, Any],
    pcf_snapshot: dict[str, Any],
) -> dict[str, Any]:
    """Join stable ETF identity/classification with the current official PCF facts.

    This is a fact-layer projection only. It intentionally excludes secondary-market
    premium, turnover, exit capacity, broker execution quality, and any opportunity score.
    """
    target_trade_date = str(pcf_snapshot.get("target_trade_date") or "")
    pcf_by_key = {
        _key(row): dict(row)
        for row in pcf_snapshot.get("rows") or []
        if isinstance(row, dict) and all(_key(row))
    }

    rows: list[dict[str, Any]] = []
    status_counter: Counter[str] = Counter()

    for identity in full_universe.get("rows") or []:
        if not isinstance(identity, dict):
            continue
        exchange, code = _key(identity)
        if not exchange or not code:
            continue
        pcf = pcf_by_key.get((exchange, code))
        if pcf is None:
            pcf_status = "MISSING"
        elif str(pcf.get("trade_date") or "") == target_trade_date:
            pcf_status = "FRESH"
        else:
            pcf_status = "STALE"
        status_counter[pcf_status] += 1

        row = {
            "exchange": exchange,
            "code": code,
            "name": identity.get("name"),
            "manager": identity.get("manager"),
            "tracking_index": identity.get("tracking_index"),
            "listing_date": identity.get("listing_date"),
            "region_scope": identity.get("region_scope"),
            "asset_class": identity.get("asset_class"),
            "strategy_style": identity.get("strategy_style"),
            "qdii_flag": identity.get("qdii_flag"),
            "pcf_status": pcf_status,
            "event_eligible": pcf_status == "FRESH",
            "target_trade_date": target_trade_date or None,
            "pcf_trade_date": pcf.get("trade_date") if pcf else None,
            "creation_allowed": pcf.get("creation_allowed") if pcf else None,
            "redemption_allowed": pcf.get("redemption_allowed") if pcf else None,
            "creation_redemption_unit": pcf.get("creation_redemption_unit") if pcf else None,
            "nav_per_cu": pcf.get("nav_per_cu") if pcf else None,
            "nav_per_share": pcf.get("nav_per_share") if pcf else None,
            "creation_limit": pcf.get("creation_limit") if pcf else None,
            "net_creation_limit": pcf.get("net_creation_limit") if pcf else None,
            "account_creation_limit": pcf.get("account_creation_limit") if pcf else None,
            "account_net_creation_limit": pcf.get("account_net_creation_limit") if pcf else None,
            "market_creation_limit": pcf.get("market_creation_limit") if pcf else None,
            "account_creation_cap": pcf.get("account_creation_cap") if pcf else None,
            "capacity_kind": _capacity_kind(pcf) if pcf else None,
            "total_baskets": pcf.get("total_baskets") if pcf else None,
            "account_baskets": pcf.get("account_baskets") if pcf else None,
            "minimum_accounts_to_fill": pcf.get("minimum_accounts_to_fill") if pcf else None,
            "basket_value": pcf.get("basket_value") if pcf else None,
            "creation_redemption_mode": pcf.get("creation_redemption_mode") if pcf else None,
            "official_pcf_page_url": identity.get("pcf_page_url"),
            "official_pcf_source_url": pcf.get("source_url") if pcf else None,
        }
        rows.append(row)

    rows.sort(key=lambda row: (row["exchange"], row["code"]))
    return {
        "schema_version": VIEW_SCHEMA_VERSION,
        "source": pcf_snapshot.get("source"),
        "target_trade_date": target_trade_date or None,
        "universe_count": len(rows),
        "fresh_count": status_counter["FRESH"],
        "stale_count": status_counter["STALE"],
        "missing_count": status_counter["MISSING"],
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build ETF primary-market fact monitor view")
    parser.add_argument("--universe", required=True)
    parser.add_argument("--pcf", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    payload = build_monitor_view(
        full_universe=_read_json(args.universe),
        pcf_snapshot=_read_json(args.pcf),
    )
    _write_json(args.output, payload)
    print(
        json.dumps(
            {
                "target_trade_date": payload["target_trade_date"],
                "universe_count": payload["universe_count"],
                "fresh_count": payload["fresh_count"],
                "stale_count": payload["stale_count"],
                "missing_count": payload["missing_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
