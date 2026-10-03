from __future__ import annotations

from datetime import datetime
import argparse
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .estimate_model_registry import (
    REGISTRY_VERSION,
    estimate_model_id,
    estimate_model_version,
    registry_snapshot,
)
from .estimate_reliability import (
    CSI_COMPONENT_METHOD,
    is_domestic_market_refresh_time,
)


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
PERSISTENCE_VERSION = "LOF_ESTIMATE_PERSISTENCE_V1"
HISTORY_MANIFEST_VERSION = "LOF_ESTIMATE_HISTORY_MANIFEST_V1"
QUARANTINE_VERSION = "LOF_ESTIMATE_HISTORY_QUARANTINE_V1"

VALID_RUNTIME = "VALID_RUNTIME"
VALID_AUDITED_BASELINE = "VALID_AUDITED_BASELINE"
LEGACY_UNVERIFIED = "LEGACY_UNVERIFIED"
QUARANTINED = "QUARANTINED"

VALIDATION_ELIGIBLE_STATUSES = {
    VALID_RUNTIME,
    VALID_AUDITED_BASELINE,
}

AUDITED_BASELINE_DATE = "2026-09-30"
AUDITED_BASELINE_METHODS = {
    "INDEX_PROXY_PREV_CLOSE",
    "CSI_COMPONENT_WEIGHT_PREV_CLOSE",
    "TARGET_ETF_PREV_CLOSE",
    "MULTIDAY_PROXY_FX_BRIDGE",
}


def _load(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return default
    return payload if isinstance(payload, dict) else default


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def enrich_history_identity(row: dict) -> dict:
    result = dict(row)
    method = result.get("estimated_nav_method")
    result["estimated_model_id"] = (
        result.get("estimated_model_id") or estimate_model_id(method)
    )
    result["estimated_model_version"] = (
        result.get("estimated_model_version") or estimate_model_version(method)
    )
    result["model_registry_version"] = REGISTRY_VERSION
    return result


def runtime_history_row(row: dict) -> dict:
    result = enrich_history_identity(row)
    result["validity_status"] = VALID_RUNTIME
    result["validity_reason"] = "RELIABLE_AVAILABLE_AT_CAPTURE"
    return result


def history_validation_eligible(row: dict, *, truth_date: str | None = None) -> bool:
    status = str(row.get("validity_status") or "")
    if status:
        return status in VALIDATION_ELIGIBLE_STATUSES

    # Backward compatibility before the V1 audit is applied: only the
    # explicitly audited 2026-09-30 baseline can seed validation.
    method = str(row.get("estimated_nav_method") or "")
    day = str(truth_date or row.get("date") or "")
    return (
        day == AUDITED_BASELINE_DATE
        and method in AUDITED_BASELINE_METHODS
    )


def _known_invalid_reason(row: dict) -> str | None:
    method = str(row.get("estimated_nav_method") or "")
    if method == CSI_COMPONENT_METHOD:
        effective_time = (
            row.get("estimated_nav_proxy_time")
            or row.get("estimated_nav_time")
        )
        if not is_domestic_market_refresh_time(effective_time):
            return "CSI_COMPONENT_OUTSIDE_DOMESTIC_REFRESH_WINDOW"
    return None


def _legacy_validity(row: dict, *, day: str) -> tuple[str, str]:
    reason = _known_invalid_reason(row)
    if reason is not None:
        return QUARANTINED, reason

    method = str(row.get("estimated_nav_method") or "")
    if day == AUDITED_BASELINE_DATE and method in AUDITED_BASELINE_METHODS:
        return VALID_AUDITED_BASELINE, "MANUALLY_AUDITED_2026_09_30_BASELINE"

    return LEGACY_UNVERIFIED, "PREDATES_PERSISTENCE_V1_AUDIT"


def write_model_registry_snapshot(data_root: str | Path) -> dict:
    root = Path(data_root)
    payload = {
        "persistence_version": PERSISTENCE_VERSION,
        **registry_snapshot(),
    }
    path = root / "estimate_model_registry.json"
    current = _load(path, {})
    if current != payload:
        _write_json(path, payload)
    return payload


def audit_estimate_history(
    data_root: str | Path,
    *,
    apply: bool = False,
    now: datetime | None = None,
) -> dict:
    root = Path(data_root)
    history_dir = root / "estimate_history"
    quarantine_dir = root / "estimate_history_quarantine"
    now = now or datetime.now(SHANGHAI_TZ)

    day_summaries: dict[str, dict] = {}
    total_active = 0
    total_quarantined = 0
    total_legacy_unverified = 0
    total_valid = 0

    for path in sorted(history_dir.glob("????-??-??.json")):
        day = path.stem
        payload = _load(
            path,
            {"version": None, "date": day, "updated_at": None, "rows": {}},
        )
        active_rows: dict[str, dict] = {}
        quarantined_rows: dict[str, dict] = {}

        for code, raw in (payload.get("rows") or {}).items():
            if not isinstance(raw, dict):
                continue
            row = enrich_history_identity(raw)
            existing_status = str(row.get("validity_status") or "")
            if existing_status in {
                VALID_RUNTIME,
                VALID_AUDITED_BASELINE,
                LEGACY_UNVERIFIED,
            }:
                status = existing_status
                reason = str(row.get("validity_reason") or "")
                known_invalid = _known_invalid_reason(row)
                if known_invalid is not None:
                    status, reason = QUARANTINED, known_invalid
            else:
                status, reason = _legacy_validity(row, day=day)

            row["validity_status"] = status
            row["validity_reason"] = reason
            if status == QUARANTINED:
                row["quarantined_from"] = str(path.name)
                row["quarantined_at"] = now.isoformat()
                quarantined_rows[str(code)] = row
            else:
                active_rows[str(code)] = row
                if status == LEGACY_UNVERIFIED:
                    total_legacy_unverified += 1
                else:
                    total_valid += 1

        total_active += len(active_rows)
        total_quarantined += len(quarantined_rows)
        day_summaries[day] = {
            "active_count": len(active_rows),
            "quarantined_count": len(quarantined_rows),
            "valid_count": sum(
                1
                for row in active_rows.values()
                if row.get("validity_status") in VALIDATION_ELIGIBLE_STATUSES
            ),
            "legacy_unverified_count": sum(
                1
                for row in active_rows.values()
                if row.get("validity_status") == LEGACY_UNVERIFIED
            ),
        }

        if not apply:
            continue

        active_payload = {
            "version": "LOF_ESTIMATE_HISTORY_V3",
            "date": day,
            "updated_at": payload.get("updated_at"),
            "model_registry_version": REGISTRY_VERSION,
            "rows": active_rows,
        }
        _write_json(path, active_payload)

        quarantine_path = quarantine_dir / path.name
        prior_quarantine = _load(
            quarantine_path,
            {
                "version": QUARANTINE_VERSION,
                "date": day,
                "rows": {},
            },
        )
        merged_quarantine = dict(prior_quarantine.get("rows") or {})
        merged_quarantine.update(quarantined_rows)
        if merged_quarantine:
            _write_json(
                quarantine_path,
                {
                    "version": QUARANTINE_VERSION,
                    "date": day,
                    "updated_at": now.isoformat(),
                    "model_registry_version": REGISTRY_VERSION,
                    "rows": merged_quarantine,
                },
            )

    manifest = {
        "version": HISTORY_MANIFEST_VERSION,
        "persistence_version": PERSISTENCE_VERSION,
        "model_registry_version": REGISTRY_VERSION,
        "audited_at": now.isoformat(),
        "applied": bool(apply),
        "active_row_count": total_active,
        "validation_eligible_count": total_valid,
        "legacy_unverified_count": total_legacy_unverified,
        "quarantined_row_count": total_quarantined,
        "days": day_summaries,
    }

    if apply:
        write_model_registry_snapshot(root)
        _write_json(root / "estimate_history_manifest.json", manifest)

    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Audit and optionally migrate LOF Estimated NAV persistence."
    )
    parser.add_argument("--data-root", required=True)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Rewrite active history, write quarantine and manifests.",
    )
    args = parser.parse_args(argv)
    result = audit_estimate_history(
        args.data_root,
        apply=args.apply,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
