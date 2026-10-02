from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .r2c_profile_summary import load_r2c_t1_profile_summary
from .research_disposition import load_research_dispositions


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
REGISTRY_VERSION = "LOF_SHADOW_REGISTRY_V0"

SHADOW_SOURCES = {
    "R2A_HOLDINGS": "r2a_shadow_snapshot.json",
    "R2C_RISK_OVERLAY": "r2c_risk_overlay_shadow.json",
    "R2C_LIVE_164814": "r2c_live_overlay/r2c_live_overlay_shadow.json",
    "R4_INDIA": "r4_india_shadow/r4_india_shadow.json",
    "R4_USD_BOND_501300": (
        "r4_usd_bond_shadow/r4_usd_bond_501300_shadow.json"
    ),
    "R2B2_CASH_HEAVY": "r2b2_cash_shadow/r2b2_cash_shadow.json",
    "R2B2_LOW_VOL_RESIDUAL": (
        "r2b2_low_vol_residual_shadow/"
        "r2b2_low_vol_165508_shadow.json"
    ),
    "R5_CROSS_BORDER_501025": (
        "r5_cross_border_501025_shadow/"
        "r5_cross_border_501025_shadow.json"
    ),
    "R5_HSI_FEEDER_501302": (
        "r5_hsi_feeder_501302_shadow/"
        "r5_hsi_feeder_501302_shadow.json"
    ),
}

VALIDATION_LEDGER = (
    "shadow_validation/shadow_validation_ledger.json"
)


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=SHANGHAI_TZ)
    return result.astimezone(SHANGHAI_TZ)


def _age_seconds(value: Any, now: datetime) -> int | None:
    parsed = _parse_dt(value)
    if parsed is None:
        return None
    return int((now - parsed).total_seconds())


def _shadow_models(
    data_root: Path,
    now: datetime,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for source, relative in SHADOW_SOURCES.items():
        payload = _load_json(data_root / relative)
        if payload is None:
            continue
        generated_at = payload.get("generated_at")
        age = _age_seconds(generated_at, now)
        for row in payload.get("rows") or []:
            code = str(
                row.get("fund_code")
                or row.get("code")
                or ""
            ).strip()
            if not code:
                continue
            item = {
                "source": source,
                "snapshot_id": payload.get("snapshot_id"),
                "generated_at": generated_at,
                "snapshot_age_seconds": age,
                "method": row.get("method") or payload.get("method"),
                "research_group": row.get("research_group"),
                "status": row.get("status"),
                "quality_candidate": (
                    row.get("quality_candidate")
                    or row.get("quality")
                ),
                "error": row.get("error"),
                "shadow_estimated_nav": row.get("shadow_estimated_nav"),
                "shadow_premium_rate": row.get("shadow_premium_rate"),
            }
            result.setdefault(code, []).append(item)
    for rows in result.values():
        rows.sort(
            key=lambda x: str(x.get("generated_at") or ""),
            reverse=True,
        )
    return result


def _validation_rows(
    data_root: Path,
) -> tuple[str | None, dict[str, list[dict[str, Any]]]]:
    payload = _load_json(data_root / VALIDATION_LEDGER)
    if payload is None:
        return None, {}
    result: dict[str, list[dict[str, Any]]] = {}
    for value in (payload.get("summaries") or {}).values():
        if not isinstance(value, dict):
            continue
        code = str(value.get("fund_code") or "").strip()
        if not code:
            continue
        result.setdefault(code, []).append(
            {
                "model_id": value.get("model_id"),
                "observation_count": value.get("observation_count"),
                "evaluated_count": value.get("evaluated_count"),
                "pending_count": value.get("pending_count"),
                "evaluated_day_count": value.get("evaluated_day_count"),
                "mae_pct": value.get("mae_pct"),
                "daily_mae_pct": value.get("daily_mae_pct"),
                "p90_abs_error_pct": value.get("p90_abs_error_pct"),
                "max_abs_error_pct": value.get("max_abs_error_pct"),
                "bias_pct": value.get("bias_pct"),
                "latest_target_nav_date": value.get(
                    "latest_target_nav_date"
                ),
            }
        )
    for rows in result.values():
        rows.sort(key=lambda x: str(x.get("model_id") or ""))
    return payload.get("updated_at"), result


def _aggregate_shadow_status(
    models: list[dict[str, Any]],
) -> str | None:
    statuses = {
        str(row.get("status") or "")
        for row in models
    }
    if "AVAILABLE" in statuses:
        return "AVAILABLE"
    if "STALE" in statuses:
        return "STALE"
    if "UNAVAILABLE" in statuses:
        return "UNAVAILABLE"
    return None


def load_shadow_registry(
    data_root: str | Path,
    *,
    main_snapshot: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(SHANGHAI_TZ)
    root = Path(data_root)
    t1_payload = load_r2c_t1_profile_summary(root)
    t1_rows = t1_payload.get("rows") or {}
    models_by_code = _shadow_models(root, now)
    ledger_updated_at, validation_by_code = _validation_rows(root)

    market_rows = (
        main_snapshot.get("rows") or []
        if main_snapshot is not None
        else []
    )
    disposition_payload = load_research_dispositions(
        root,
        market_rows=market_rows,
    )
    dispositions = disposition_payload.get("rows") or {}

    rows: dict[str, dict[str, Any]] = {}
    main_count = 0
    t1_count = 0
    shadow_count = 0
    deferred_count = 0
    unresolved_count = 0

    for market in market_rows:
        code = str(market.get("code") or "").strip()
        if not code:
            continue
        main_available_now = (
            market.get("estimated_nav_status") == "AVAILABLE"
        )
        main_method = str(
            market.get("estimated_nav_method") or ""
        ).strip()
        main_covered = (
            main_available_now
            or (
                bool(main_method)
                and main_method != "UNAVAILABLE"
            )
        )
        t1_profile = t1_rows.get(code)
        models = models_by_code.get(code, [])
        validations = validation_by_code.get(code, [])
        disposition = dispositions.get(code)

        layers: list[str] = []
        if main_covered:
            layers.append("MAIN_ESTIMATE")
            main_count += 1
        if t1_profile is not None:
            layers.append("T1_PROFILE")
            t1_count += 1
        if models:
            layers.append("ACTIVE_SHADOW")
            shadow_count += 1
        deferred = bool(
            disposition is not None
            and not main_covered
            and t1_profile is None
            and not models
        )
        if deferred:
            layers.append("RESEARCHED_DEFERRED")
            deferred_count += 1

        if main_covered:
            display_state = "MAIN_ESTIMATE"
        elif models:
            display_state = "ACTIVE_SHADOW"
        elif t1_profile is not None:
            display_state = "T1_PROFILE"
        elif deferred:
            display_state = "RESEARCHED_DEFERRED"
        else:
            display_state = "UNRESOLVED"
            unresolved_count += 1

        evaluated_count = sum(
            int(item.get("evaluated_count") or 0)
            for item in validations
        )
        maes = [
            float(item["mae_pct"])
            for item in validations
            if item.get("mae_pct") is not None
        ]
        latest_generated_at = (
            models[0].get("generated_at")
            if models
            else None
        )

        rows[code] = {
            "fund_code": code,
            "fund_name": market.get("name"),
            "display_state": display_state,
            "coverage_layers": layers,
            "main_estimate_covered": main_covered,
            "main_estimate_available_now": main_available_now,
            "main_estimate_method": (
                main_method if main_covered else None
            ),
            "t1_profile_available": t1_profile is not None,
            "shadow_active": bool(models),
            "shadow_model_count": len(models),
            "shadow_status": _aggregate_shadow_status(models),
            "shadow_latest_generated_at": latest_generated_at,
            "shadow_latest_age_seconds": (
                models[0].get("snapshot_age_seconds")
                if models
                else None
            ),
            "models": models,
            "validation": validations,
            "validation_evaluated_count": evaluated_count,
            "validation_best_mae_pct": min(maes) if maes else None,
            "research_disposition": disposition,
        }

    return {
        "status": "PASS" if main_snapshot is not None else "NO_SNAPSHOT",
        "version": REGISTRY_VERSION,
        "generated_at": now.isoformat(),
        "market_snapshot_id": (
            main_snapshot.get("snapshot_id")
            if main_snapshot is not None
            else None
        ),
        "validation_ledger_updated_at": ledger_updated_at,
        "research_disposition_status": disposition_payload.get("status"),
        "research_disposition_knowledge_commit_sha": (
            disposition_payload.get("knowledge_commit_sha")
        ),
        "summary": {
            "universe_count": len(rows),
            "main_estimate_count": main_count,
            "main_estimate_covered_count": main_count,
            "main_estimate_available_now_count": sum(
                row.get("estimated_nav_status") == "AVAILABLE"
                for row in market_rows
            ),
            "t1_profile_count": t1_count,
            "active_shadow_fund_count": shadow_count,
            "researched_deferred_count": deferred_count,
            "unresolved_count": unresolved_count,
        },
        "rows": rows,
    }
