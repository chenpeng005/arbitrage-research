from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
RELIABLE_SUBSCRIPTION_STATES = {"OPEN", "LIMITED", "SUSPENDED"}
RELIABLE_LIMIT_TYPES = {"NUMERIC", "UNLIMITED"}


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        return result.replace(tzinfo=SHANGHAI_TZ)
    return result


def _trade_date(snapshot: dict) -> str:
    cutoff = _parse_datetime(snapshot.get("market_cutoff"))
    if cutoff is None:
        raise ValueError("snapshot.market_cutoff is required")
    return cutoff.astimezone(SHANGHAI_TZ).date().isoformat()


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _semantic_limit(row: dict) -> dict | None:
    limit_type = str(row.get("daily_subscription_limit_type") or "UNKNOWN")
    if limit_type not in RELIABLE_LIMIT_TYPES:
        return None
    amount = _float_or_none(row.get("daily_subscription_limit"))
    if limit_type == "NUMERIC" and amount is None:
        return None
    return {
        "type": limit_type,
        "amount": amount,
        "raw": row.get("daily_subscription_limit_raw"),
    }


def _limit_equal(left: dict | None, right: dict | None) -> bool:
    if left is None or right is None:
        return left is right
    return left.get("type") == right.get("type") and left.get("amount") == right.get("amount")


def _event_id(*parts: Any) -> str:
    payload = "|".join("" if value is None else str(value) for value in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _ranking_row(row: dict, *, basis: str) -> dict:
    common = {
        "code": row.get("code"),
        "name": row.get("name"),
        "exchange": row.get("exchange"),
        "price": _float_or_none(row.get("price")),
        "quote_time": row.get("quote_time"),
    }
    if basis == "OFFICIAL_NAV":
        return {
            **common,
            "official_nav": _float_or_none(row.get("official_nav")),
            "static_premium_rate": _float_or_none(row.get("static_premium_rate")),
            "official_nav_date": row.get("official_nav_date"),
            "official_nav_lag_label": row.get("official_nav_lag_label"),
        }
    return {
        **common,
        "estimated_nav": _float_or_none(row.get("estimated_nav")),
        "estimated_premium_rate": _float_or_none(row.get("estimated_premium_rate")),
        "estimated_nav_time": row.get("estimated_nav_time"),
        "estimated_nav_quality": row.get("estimated_nav_quality"),
        "estimated_nav_method": row.get("estimated_nav_method"),
    }


def _top_and_bottom(
    rows: list[dict],
    *,
    value_key: str,
    limit: int = 5,
) -> tuple[list[dict], list[dict]]:
    positive = [row for row in rows if row.get(value_key) is not None and row[value_key] > 0]
    negative = [row for row in rows if row.get(value_key) is not None and row[value_key] < 0]
    premium = sorted(positive, key=lambda row: (-row[value_key], str(row.get("code") or "")))[:limit]
    discount = sorted(negative, key=lambda row: (row[value_key], str(row.get("code") or "")))[:limit]
    for rank, row in enumerate(premium, start=1):
        row["rank"] = rank
    for rank, row in enumerate(discount, start=1):
        row["rank"] = rank
    return premium, discount


class LofDailyReminderStore:
    """Durable A/B change ledger plus C/D end-of-day ranking projection."""

    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)
        self.root = self.data_root / "daily_reminder"
        self.events_dir = self.root / "events"
        self.candidates_dir = self.root / "candidates"
        self.daily_dir = self.root / "daily"
        self.checkpoint_path = self.root / "checkpoint.json"
        self.latest_path = self.root / "latest_daily_reminder.json"

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _load_checkpoint(self) -> dict:
        if not self.checkpoint_path.exists():
            return {"version": "LOF_DAILY_REMINDER_CHECKPOINT_V1", "funds": {}}
        value = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("daily reminder checkpoint must be an object")
        value.setdefault("funds", {})
        return value

    def _persist_checkpoint(self, checkpoint: dict) -> None:
        payload = json.dumps(checkpoint, ensure_ascii=False, separators=(",", ":"), default=_json_default) + "\n"
        self._atomic_write(self.checkpoint_path, payload)

    def _event_path(self, trade_date: str) -> Path:
        return self.events_dir / f"{trade_date}.jsonl"

    def _load_event_ids(self, trade_date: str) -> set[str]:
        path = self._event_path(trade_date)
        if not path.exists():
            return set()
        result: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("event_id"):
                result.add(str(row["event_id"]))
        return result

    def _append_event(self, trade_date: str, event: dict, known_ids: set[str]) -> bool:
        event_id = str(event["event_id"])
        if event_id in known_ids:
            return False
        path = self._event_path(trade_date)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=_json_default) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        known_ids.add(event_id)
        return True

    def _persist_candidate(self, snapshot: dict, trade_date: str) -> bool:
        quality = snapshot.get("quality_summary") or {}
        fresh_count = int(quality.get("quote_fresh_count") or 0)
        if fresh_count <= 0:
            return False

        official_rows: list[dict] = []
        estimated_rows: list[dict] = []
        for row in snapshot.get("rows") or []:
            if (
                row.get("quote_status") == "FRESH"
                and row.get("official_nav_status") == "AVAILABLE"
                and row.get("price") is not None
                and row.get("official_nav") is not None
                and row.get("static_premium_rate") is not None
            ):
                official_rows.append(_ranking_row(row, basis="OFFICIAL_NAV"))
            if (
                row.get("quote_status") == "FRESH"
                and row.get("estimated_nav_status") == "AVAILABLE"
                and row.get("price") is not None
                and row.get("estimated_nav") is not None
                and row.get("estimated_premium_rate") is not None
            ):
                estimated_rows.append(_ranking_row(row, basis="ESTIMATED_NAV"))

        candidate = {
            "trade_date": trade_date,
            "snapshot_id": snapshot.get("snapshot_id"),
            "snapshot_market_cutoff": snapshot.get("market_cutoff"),
            "quote_fresh_count": fresh_count,
            "universe_count": snapshot.get("universe_count"),
            "official_rows": official_rows,
            "estimated_rows": estimated_rows,
        }
        path = self.candidates_dir / f"{trade_date}.json"
        payload = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), default=_json_default) + "\n"
        self._atomic_write(path, payload)
        return True

    def observe_snapshot(self, snapshot: dict) -> list[dict]:
        """Consume one already-persisted formal market snapshot."""
        trade_date = _trade_date(snapshot)
        checkpoint = self._load_checkpoint()
        funds = checkpoint.setdefault("funds", {})
        known_ids = self._load_event_ids(trade_date)
        emitted: list[dict] = []

        for row in snapshot.get("rows") or []:
            code = str(row.get("code") or "").strip()
            exchange = str(row.get("exchange") or "").strip()
            if not code:
                continue
            key = f"{exchange}:{code}" if exchange else code
            fund = funds.setdefault(key, {})
            fund["code"] = code
            fund["exchange"] = exchange or None
            fund["name"] = row.get("name")

            observed_at = row.get("state_time") or snapshot.get("market_cutoff")
            current_status = str(row.get("subscription_status") or "UNKNOWN")
            old_status = fund.get("subscription_status")
            status_changed = False

            if current_status in RELIABLE_SUBSCRIPTION_STATES:
                if old_status in RELIABLE_SUBSCRIPTION_STATES and old_status != current_status:
                    event = {
                        "event_id": _event_id("A", key, old_status, current_status, observed_at),
                        "event_type": "SUBSCRIPTION_STATUS_CHANGE",
                        "trade_date": trade_date,
                        "code": code,
                        "name": row.get("name"),
                        "exchange": exchange or None,
                        "old_status": old_status,
                        "new_status": current_status,
                        "first_observed_time": observed_at,
                        "state_source": row.get("state_source"),
                    }
                    if self._append_event(trade_date, event, known_ids):
                        emitted.append(event)
                    status_changed = True
                fund["subscription_status"] = current_status
                fund["subscription_status_observed_at"] = observed_at

            current_limit = _semantic_limit(row)
            old_limit = fund.get("limit")
            if current_status != "SUSPENDED" and current_limit is not None:
                if old_limit is not None and not _limit_equal(old_limit, current_limit):
                    event = {
                        "event_id": _event_id(
                            "B",
                            key,
                            old_limit.get("type"),
                            old_limit.get("amount"),
                            current_limit.get("type"),
                            current_limit.get("amount"),
                            observed_at,
                        ),
                        "event_type": "SUBSCRIPTION_LIMIT_CHANGE",
                        "trade_date": trade_date,
                        "code": code,
                        "name": row.get("name"),
                        "exchange": exchange or None,
                        "old_limit_type": old_limit.get("type"),
                        "old_limit_amount": old_limit.get("amount"),
                        "old_limit_raw": old_limit.get("raw"),
                        "new_limit_type": current_limit.get("type"),
                        "new_limit_amount": current_limit.get("amount"),
                        "new_limit_raw": current_limit.get("raw"),
                        "first_observed_time": observed_at,
                        "state_source": row.get("state_source"),
                    }
                    if self._append_event(trade_date, event, known_ids):
                        emitted.append(event)
                fund["limit"] = current_limit
                fund["limit_observed_at"] = observed_at
            elif status_changed and current_status == "SUSPENDED":
                # Preserve last reliable business limit; do not emit a mechanical
                # B event when suspension projects the effective limit to N/A.
                pass

        checkpoint["updated_at"] = snapshot.get("market_cutoff")
        self._persist_checkpoint(checkpoint)
        self._persist_candidate(snapshot, trade_date)
        return emitted

    def _load_events(self, trade_date: str) -> list[dict]:
        path = self._event_path(trade_date)
        if not path.exists():
            return []
        result: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                result.append(json.loads(line))
        return result

    def build_daily_reminder(self, trade_date: str) -> dict:
        candidate_path = self.candidates_dir / f"{trade_date}.json"
        if not candidate_path.exists():
            raise FileNotFoundError(f"no reliable LOF snapshot candidate for {trade_date}")
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        events = self._load_events(trade_date)

        a_events = [row for row in events if row.get("event_type") == "SUBSCRIPTION_STATUS_CHANGE"]
        b_events = [row for row in events if row.get("event_type") == "SUBSCRIPTION_LIMIT_CHANGE"]
        official_premium, official_discount = _top_and_bottom(
            list(candidate.get("official_rows") or []),
            value_key="static_premium_rate",
        )
        estimated_premium, estimated_discount = _top_and_bottom(
            list(candidate.get("estimated_rows") or []),
            value_key="estimated_premium_rate",
        )

        reminder = {
            "contract_version": "LOF_DAILY_REMINDER_V0_1",
            "trade_date": trade_date,
            "snapshot_id": candidate.get("snapshot_id"),
            "snapshot_market_cutoff": candidate.get("snapshot_market_cutoff"),
            "A_subscription_status_changes": a_events,
            "B_subscription_limit_changes": b_events,
            "C_official_premium_top5": official_premium,
            "C_official_discount_top5": official_discount,
            "D_estimated_premium_top5": estimated_premium,
            "D_estimated_discount_top5": estimated_discount,
        }
        payload = json.dumps(reminder, ensure_ascii=False, indent=2, default=_json_default) + "\n"
        daily_path = self.daily_dir / f"{trade_date}.json"
        self._atomic_write(daily_path, payload)
        self._atomic_write(self.latest_path, payload)
        return reminder

    def load_daily_reminder(self, trade_date: str) -> dict | None:
        path = self.daily_dir / f"{trade_date}.json"
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("daily reminder must be an object")
        return value

    def load_latest_daily_reminder(self) -> dict | None:
        if not self.latest_path.exists():
            return None
        value = json.loads(self.latest_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("latest daily reminder must be an object")
        return value


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build LOF daily reminder snapshot.")
    parser.add_argument("--data-root", required=True)
    parser.add_argument("--date", dest="trade_date")
    parser.add_argument(
        "--skip-if-missing",
        action="store_true",
        help="Exit successfully when the requested date has no reliable candidate.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    trade_date = args.trade_date or datetime.now(SHANGHAI_TZ).date().isoformat()
    store = LofDailyReminderStore(args.data_root)
    try:
        reminder = store.build_daily_reminder(trade_date)
    except FileNotFoundError as exc:
        if not args.skip_if_missing:
            raise
        print(
            json.dumps(
                {
                    "status": "SKIP_NO_CANDIDATE",
                    "trade_date": trade_date,
                    "detail": str(exc),
                },
                ensure_ascii=False,
            )
        )
        return 0
    print(json.dumps(reminder, ensure_ascii=False, default=_json_default))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
