from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Iterable

from .estimate_model_registry import estimate_model_id, estimate_model_version
from .estimate_persistence import write_model_registry_snapshot
from .estimate_reliability import is_reliable_available_estimate
from .snapshot_archive import (
    iter_snapshot_paths,
    read_snapshot_text,
    write_snapshot_gzip,
)
from .snapshot_retention import maybe_apply_snapshot_retention
from .estimate_validation import (
    record_estimate_history,
    update_estimate_validation_ledger,
)


def _json_default(value: Any):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError(f"not JSON serializable: {type(value).__name__}")


def snapshot_to_json(snapshot: dict) -> str:
    return json.dumps(
        snapshot,
        ensure_ascii=False,
        separators=(",", ":"),
        default=_json_default,
    )


def snapshot_from_json(text: str) -> dict:
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("snapshot payload must be an object")
    return value


LAST_ESTIMATE_VERSION = "LOF_LAST_ESTIMATE_V2"


def _as_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _time_key(value: Any) -> str:
    return str(value or "")


class LofSnapshotStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.snapshots_dir = self.root / "snapshots"
        self.latest_path = self.root / "latest_market_snapshot.json"
        self.last_estimates_path = self.root / "last_estimated_nav.json"

    def persist(self, snapshot: dict) -> Path:
        snapshot_id = str(snapshot.get("snapshot_id") or "").strip()
        if not snapshot_id:
            raise ValueError("snapshot_id is required")

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.root.mkdir(parents=True, exist_ok=True)

        payload = snapshot_to_json(snapshot) + "\n"
        archive_path = self.snapshots_dir / f"{snapshot_id}.json.gz"
        write_snapshot_gzip(archive_path, payload)
        self._atomic_write(self.latest_path, payload)
        try:
            self.update_last_estimates(snapshot)
        except Exception:
            # Presentation history must never block the market snapshot lane.
            pass
        try:
            record_estimate_history(self.root, snapshot)
            update_estimate_validation_ledger(self.root, snapshot)
            write_model_registry_snapshot(self.root)
        except Exception:
            # Validation bookkeeping is evidence-only and must never block
            # the market snapshot lane.
            pass
        try:
            generated_at = snapshot.get("generated_at")
            if isinstance(generated_at, datetime):
                retention_as_of = generated_at
            else:
                retention_as_of = datetime.fromisoformat(str(generated_at))
            maybe_apply_snapshot_retention(
                self.root,
                as_of=retention_as_of,
            )
        except Exception:
            # Retention failure must never block live snapshot persistence.
            pass
        return archive_path

    def load_latest(self) -> dict | None:
        if not self.latest_path.exists():
            return None
        return snapshot_from_json(
            self.latest_path.read_text(encoding="utf-8")
        )

    def load_last_estimates(self) -> dict:
        if not self.last_estimates_path.exists():
            return {
                "version": LAST_ESTIMATE_VERSION,
                "updated_at": None,
                "rows": {},
            }
        try:
            payload = snapshot_from_json(
                self.last_estimates_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, json.JSONDecodeError):
            return {
                "version": LAST_ESTIMATE_VERSION,
                "updated_at": None,
                "rows": {},
            }
        rows = payload.get("rows")
        if not isinstance(rows, dict):
            rows = {}
        normalized_rows = {}
        for code, raw in rows.items():
            if not isinstance(raw, dict):
                continue
            value = dict(raw)
            method = value.get("estimated_nav_method")
            if method:
                value["estimated_model_id"] = (
                    value.get("estimated_model_id")
                    or estimate_model_id(method)
                )
                value["estimated_model_version"] = (
                    value.get("estimated_model_version")
                    or estimate_model_version(method)
                )
            normalized_rows[str(code)] = value
        return {
            "version": LAST_ESTIMATE_VERSION,
            "updated_at": payload.get("updated_at"),
            "rows": normalized_rows,
        }

    @staticmethod
    def _merge_last_estimates(state: dict, snapshot: dict) -> dict:
        rows = dict(state.get("rows") or {})
        snapshot_time = snapshot.get("generated_at")
        snapshot_id = snapshot.get("snapshot_id")

        for row in snapshot.get("rows") or []:
            code = str(row.get("code") or "").strip()
            nav = _as_decimal(row.get("estimated_nav"))
            status = str(row.get("estimated_nav_status") or "")
            if not code or nav is None or not is_reliable_available_estimate(row):
                continue

            estimate_time = (
                row.get("estimated_nav_time")
                or row.get("quote_time")
                or snapshot_time
            )
            existing = rows.get(code) or {}
            if _time_key(estimate_time) < _time_key(
                existing.get("estimated_nav_time")
            ):
                continue

            premium = _as_decimal(row.get("estimated_premium_rate"))
            price = _as_decimal(row.get("price"))
            if premium is None and price is not None and nav > 0:
                premium = (price / nav - Decimal("1")) * Decimal("100")

            rows[code] = {
                "code": code,
                "name": row.get("name"),
                "estimated_nav": nav,
                "estimated_premium_rate": premium,
                "estimated_nav_time": estimate_time,
                "estimated_nav_status": status,
                "estimated_nav_method": row.get("estimated_nav_method"),
                "estimated_model_id": (
                    row.get("estimated_model_id")
                    or estimate_model_id(row.get("estimated_nav_method"))
                ),
                "estimated_model_version": (
                    row.get("estimated_model_version")
                    or estimate_model_version(row.get("estimated_nav_method"))
                ),
                "estimated_nav_quality": row.get("estimated_nav_quality"),
                "estimated_nav_proxy": row.get("estimated_nav_proxy"),
                "estimated_nav_proxy_time": row.get(
                    "estimated_nav_proxy_time"
                ),
                "estimated_nav_proxy_return": row.get(
                    "estimated_nav_proxy_return"
                ),
                "estimated_nav_fx_return": row.get(
                    "estimated_nav_fx_return"
                ),
                "estimated_nav_fx_time": row.get(
                    "estimated_nav_fx_time"
                ),
                "estimated_nav_fx_source": row.get(
                    "estimated_nav_fx_source"
                ),
                "estimated_nav_exposure_ratio": row.get(
                    "estimated_nav_exposure_ratio"
                ),
                "estimated_nav_tracking_adjustment": row.get(
                    "estimated_nav_tracking_adjustment"
                ),
                "resolver_class": row.get("resolver_class"),
                "anchor_official_nav": row.get("official_nav"),
                "anchor_official_nav_date": row.get("official_nav_date"),
                "price": price,
                "quote_time": row.get("quote_time"),
                "source_snapshot_id": snapshot_id,
                "snapshot_generated_at": snapshot_time,
            }

        return {
            "version": LAST_ESTIMATE_VERSION,
            "updated_at": snapshot_time or state.get("updated_at"),
            "rows": rows,
        }

    def update_last_estimates(self, snapshot: dict) -> dict:
        state = self._merge_last_estimates(
            self.load_last_estimates(),
            snapshot,
        )
        payload = snapshot_to_json(state) + "\n"
        self._atomic_write(self.last_estimates_path, payload)
        return state

    def rebuild_last_estimates(
        self,
        snapshot_paths: Iterable[str | Path] | None = None,
    ) -> dict:
        paths = (
            [Path(p) for p in snapshot_paths]
            if snapshot_paths is not None
            else iter_snapshot_paths(self.root)
        )
        state = {
            "version": LAST_ESTIMATE_VERSION,
            "updated_at": None,
            "rows": {},
        }
        for path in paths:
            try:
                snapshot = snapshot_from_json(
                    read_snapshot_text(path)
                )
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            state = self._merge_last_estimates(state, snapshot)
        payload = snapshot_to_json(state) + "\n"
        self._atomic_write(self.last_estimates_path, payload)
        return state

    def enrich_with_last_estimates(self, snapshot: dict) -> dict:
        state = self.load_last_estimates()
        history = state.get("rows") or {}
        result = dict(snapshot)
        enriched_rows = []
        for row in snapshot.get("rows") or []:
            value = dict(row)
            last = history.get(str(row.get("code") or "")) or {}
            value.update(
                {
                    "last_estimated_nav": last.get("estimated_nav"),
                    "last_estimated_premium_rate": last.get(
                        "estimated_premium_rate"
                    ),
                    "last_estimated_nav_time": last.get(
                        "estimated_nav_time"
                    ),
                    "last_estimated_nav_status": last.get(
                        "estimated_nav_status"
                    ),
                    "last_estimated_nav_method": last.get(
                        "estimated_nav_method"
                    ),
                    "last_estimated_model_id": last.get(
                        "estimated_model_id"
                    ),
                    "last_estimated_model_version": last.get(
                        "estimated_model_version"
                    ),
                    "last_estimated_nav_quality": last.get(
                        "estimated_nav_quality"
                    ),
                    "last_estimated_nav_proxy": last.get(
                        "estimated_nav_proxy"
                    ),
                    "last_estimated_nav_proxy_time": last.get(
                        "estimated_nav_proxy_time"
                    ),
                    "last_estimated_nav_proxy_return": last.get(
                        "estimated_nav_proxy_return"
                    ),
                    "last_estimated_nav_fx_return": last.get(
                        "estimated_nav_fx_return"
                    ),
                    "last_estimated_nav_fx_time": last.get(
                        "estimated_nav_fx_time"
                    ),
                    "last_estimated_nav_fx_source": last.get(
                        "estimated_nav_fx_source"
                    ),
                    "last_estimated_nav_exposure_ratio": last.get(
                        "estimated_nav_exposure_ratio"
                    ),
                    "last_estimated_nav_tracking_adjustment": last.get(
                        "estimated_nav_tracking_adjustment"
                    ),
                    "last_estimated_resolver_class": last.get(
                        "resolver_class"
                    ),
                    "last_estimated_anchor_nav": last.get(
                        "anchor_official_nav"
                    ),
                    "last_estimated_anchor_nav_date": last.get(
                        "anchor_official_nav_date"
                    ),
                    "last_estimated_price": last.get("price"),
                    "last_estimated_quote_time": last.get("quote_time"),
                    "last_estimated_source_snapshot_id": last.get(
                        "source_snapshot_id"
                    ),
                }
            )
            enriched_rows.append(value)
        result["rows"] = enriched_rows
        result["last_estimate_count"] = len(history)
        result["last_estimates_updated_at"] = state.get("updated_at")
        return result

    @staticmethod
    def _atomic_write(path: Path, payload: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
