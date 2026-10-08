from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import MonitorEvent, PcfSnapshot


def _ratio(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None or previous <= 0:
        return None
    return current / previous


def detect_events(
    previous: PcfSnapshot | None,
    current: PcfSnapshot,
    *,
    basket_jump_ratio: float = 2.0,
    basket_jump_absolute: float = 10.0,
) -> list[MonitorEvent]:
    """Detect PCF changes worth surfacing to a human.

    Creation-cap comparisons are only made when the binding rule type is stable.
    Cumulative and net limits have different economics and must not be compared as
    if they were the same capacity concept.
    """
    events: list[MonitorEvent] = []
    if previous is None:
        return events

    if previous.creation_allowed is False and current.creation_allowed is True:
        events.append(
            MonitorEvent(
                current.code,
                current.exchange,
                "CREATION_RESUMED",
                "HIGH",
                "ETF creation changed from closed to open.",
            )
        )

    prev_kind = previous.market_capacity_kind()
    cur_kind = current.market_capacity_kind()
    market_kind_stable = prev_kind == cur_kind

    if not market_kind_stable:
        events.append(
            MonitorEvent(
                current.code,
                current.exchange,
                "CAPACITY_RULE_CHANGED",
                "MEDIUM",
                f"Binding market creation-cap rule changed from {prev_kind or 'NONE'} to {cur_kind or 'NONE'}.",
                prev_kind,
                cur_kind,
            )
        )
    else:
        prev_total = previous.total_baskets()
        cur_total = current.total_baskets()
        ratio = _ratio(prev_total, cur_total)
        if (
            prev_total is not None
            and cur_total is not None
            and cur_total - prev_total >= basket_jump_absolute
            and (ratio is None or ratio >= basket_jump_ratio)
        ):
            kind_label = cur_kind or "UNSPECIFIED"
            events.append(
                MonitorEvent(
                    current.code,
                    current.exchange,
                    "TOTAL_CAPACITY_JUMP",
                    "HIGH",
                    f"Binding no-offset creation capacity ({kind_label}) increased from {prev_total} to {cur_total} whole baskets.",
                    prev_total,
                    cur_total,
                )
            )

    prev_account_kind = previous.account_capacity_kind()
    cur_account_kind = current.account_capacity_kind()
    account_kind_stable = prev_account_kind == cur_account_kind

    if not account_kind_stable:
        events.append(
            MonitorEvent(
                current.code,
                current.exchange,
                "ACCOUNT_CAP_RULE_CHANGED",
                "MEDIUM",
                f"Binding account creation-cap rule changed from {prev_account_kind or 'NONE'} to {cur_account_kind or 'NONE'}.",
                prev_account_kind,
                cur_account_kind,
            )
        )
    else:
        prev_account = previous.account_baskets()
        cur_account = current.account_baskets()
        prev_accounts = previous.minimum_accounts_to_fill()
        cur_accounts = current.minimum_accounts_to_fill()

        if (
            prev_account is not None
            and cur_account is not None
            and cur_account < prev_account
            and cur_account <= 1
        ):
            events.append(
                MonitorEvent(
                    current.code,
                    current.exchange,
                    "ACCOUNT_LIMIT_IMPROVED",
                    "HIGH",
                    f"Binding no-offset per-account cap tightened from {prev_account} to {cur_account} whole baskets.",
                    prev_account,
                    cur_account,
                )
            )
        elif (
            market_kind_stable
            and prev_accounts is not None
            and cur_accounts is not None
            and cur_accounts > prev_accounts
        ):
            events.append(
                MonitorEvent(
                    current.code,
                    current.exchange,
                    "ACCOUNT_DISTRIBUTION_IMPROVED",
                    "MEDIUM",
                    f"Minimum accounts required to fill no-offset executable capacity rose from {prev_accounts} to {cur_accounts}.",
                    prev_accounts,
                    cur_accounts,
                )
            )

    prev_unit = previous.creation_redemption_unit
    cur_unit = current.creation_redemption_unit
    if prev_unit is not None and cur_unit is not None and cur_unit > 0 and cur_unit < prev_unit:
        events.append(
            MonitorEvent(
                current.code,
                current.exchange,
                "CREATION_UNIT_REDUCED",
                "MEDIUM",
                f"Creation unit fell from {prev_unit} to {cur_unit} shares.",
                prev_unit,
                cur_unit,
            )
        )

    if current.creation_allowed is False and previous.creation_allowed is True:
        events.append(
            MonitorEvent(
                current.code,
                current.exchange,
                "CREATION_SUSPENDED",
                "LOW",
                "ETF creation changed from open to closed.",
            )
        )

    return events


def save_snapshots(path: str | Path, snapshots: Iterable[PcfSnapshot]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = [snapshot.to_dict() for snapshot in snapshots]
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_snapshots(path: str | Path) -> dict[tuple[str, str], PcfSnapshot]:
    target = Path(path)
    if not target.exists():
        return {}
    rows = json.loads(target.read_text(encoding="utf-8"))
    result: dict[tuple[str, str], PcfSnapshot] = {}
    model_fields = set(PcfSnapshot.__dataclass_fields__)
    for row in rows:
        payload = {k: v for k, v in row.items() if k in model_fields}
        snapshot = PcfSnapshot(**payload)
        result[(snapshot.exchange, snapshot.code)] = snapshot
    return result
