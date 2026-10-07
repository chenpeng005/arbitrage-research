from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Any


COMPARISON_SCHEMA_VERSION = 1


def _read_json_gz(path: Path) -> dict[str, Any]:
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"snapshot is not an object: {path}")
    return payload


def _row_key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("exchange") or ""), str(row.get("code") or "")


def _fact(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "creation_allowed": row.get("creation_allowed"),
        "total_baskets": row.get("total_baskets"),
        "account_baskets": row.get("account_baskets"),
        "market_capacity_kind": row.get("market_capacity_kind"),
        "account_capacity_kind": row.get("account_capacity_kind"),
        "basket_value": row.get("basket_value"),
        "creation_redemption_unit": row.get("creation_redemption_unit"),
    }


def build_comparison(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    current_trade_date = str(current.get("trade_date") or "")
    previous_trade_date = str((previous or {}).get("trade_date") or "") or None

    previous_rows = {
        _row_key(row): row
        for row in ((previous or {}).get("rows") or [])
        if isinstance(row, dict)
    }
    result_rows: list[dict[str, Any]] = []

    for current_row in current.get("rows") or []:
        if not isinstance(current_row, dict):
            continue
        key = _row_key(current_row)
        previous_row = previous_rows.get(key)
        previous_fact = _fact(previous_row)
        current_fact = _fact(current_row)
        assert current_fact is not None

        prev_total = previous_fact.get("total_baskets") if previous_fact else None
        cur_total = current_fact.get("total_baskets")
        prev_kind = previous_fact.get("market_capacity_kind") if previous_fact else None
        cur_kind = current_fact.get("market_capacity_kind")
        capacity_comparable = previous_fact is not None and prev_kind == cur_kind

        basket_delta: int | None = None
        basket_ratio: float | None = None
        if (
            capacity_comparable
            and isinstance(prev_total, int)
            and isinstance(cur_total, int)
        ):
            basket_delta = cur_total - prev_total
            if prev_total > 0:
                basket_ratio = cur_total / prev_total

        prev_account = previous_fact.get("account_baskets") if previous_fact else None
        cur_account = current_fact.get("account_baskets")
        account_basket_delta: int | None = None
        if (
            previous_fact is not None
            and previous_fact.get("account_capacity_kind") == current_fact.get("account_capacity_kind")
            and isinstance(prev_account, int)
            and isinstance(cur_account, int)
        ):
            account_basket_delta = cur_account - prev_account

        creation_changed = (
            previous_fact is not None
            and previous_fact.get("creation_allowed") != current_fact.get("creation_allowed")
        )
        capacity_rule_changed = previous_fact is not None and prev_kind != cur_kind
        account_rule_changed = (
            previous_fact is not None
            and previous_fact.get("account_capacity_kind") != current_fact.get("account_capacity_kind")
        )
        changed = bool(
            creation_changed
            or capacity_rule_changed
            or account_rule_changed
            or (basket_delta not in (None, 0))
            or (account_basket_delta not in (None, 0))
        )

        result_rows.append(
            {
                "exchange": key[0],
                "code": key[1],
                "previous": previous_fact,
                "current": current_fact,
                "capacity_comparable": capacity_comparable,
                "basket_delta": basket_delta,
                "basket_ratio": basket_ratio,
                "account_basket_delta": account_basket_delta,
                "creation_changed": creation_changed,
                "capacity_rule_changed": capacity_rule_changed,
                "account_rule_changed": account_rule_changed,
                "changed": changed,
            }
        )

    result_rows.sort(key=lambda row: (row["exchange"], row["code"]))
    return {
        "schema_version": COMPARISON_SCHEMA_VERSION,
        "previous_trade_date": previous_trade_date,
        "current_trade_date": current_trade_date,
        "row_count": len(result_rows),
        "changed_count": sum(1 for row in result_rows if row["changed"]),
        "rows": result_rows,
    }


def build_from_state_dir(state_dir: Path) -> dict[str, Any]:
    snapshots = sorted((state_dir / "snapshots").glob("*.json.gz"))
    if not snapshots:
        raise ValueError(f"no ETF primary snapshots under {state_dir / 'snapshots'}")
    current = _read_json_gz(snapshots[-1])
    previous = _read_json_gz(snapshots[-2]) if len(snapshots) >= 2 else None
    return build_comparison(previous, current)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build ETF primary previous-vs-current comparison view")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    payload = build_from_state_dir(Path(args.state_dir))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "previous_trade_date": payload["previous_trade_date"],
                "current_trade_date": payload["current_trade_date"],
                "row_count": payload["row_count"],
                "changed_count": payload["changed_count"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
