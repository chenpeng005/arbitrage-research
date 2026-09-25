from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from runtime.market_map.output_contract import (
    CONTRACT_VERSION,
    classify_zone,
)

RESOLVER_VERSION = "bond-valuation-resolver-v1"


class ResolverError(ValueError):
    """Deterministic contract/input failure in Bond Valuation Resolver."""


def _finite_positive(value: Any, field: str) -> float:
    try:
        number = float(value)
    except Exception as exc:
        raise ResolverError(f"{field} must be a finite positive number") from exc
    if not math.isfinite(number) or number <= 0:
        raise ResolverError(f"{field} must be a finite positive number")
    return number


def _finite(value: Any, field: str) -> float:
    try:
        number = float(value)
    except Exception as exc:
        raise ResolverError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise ResolverError(f"{field} must be finite")
    return number


def _load_json(path: Path, field: str) -> dict[str, Any]:
    if not path.exists():
        raise ResolverError(f"missing {field}: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ResolverError(f"invalid {field}: {path}") from exc
    if not isinstance(payload, dict):
        raise ResolverError(f"{field} must be a JSON object")
    return payload


def load_contract(
    manifest_path: Path,
    *,
    require_formal: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], pd.DataFrame]:
    manifest_path = Path(manifest_path)
    manifest = _load_json(manifest_path, "manifest.json")

    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise ResolverError(
            f"unsupported contract_version={manifest.get('contract_version')!r}"
        )

    validation = manifest.get("validation") or {}
    if validation.get("status") != "PASS":
        raise ResolverError("Market Map Output Contract validation.status must be PASS")

    if require_formal and manifest.get("snapshot_class") != "FORMAL_CLOSE":
        raise ResolverError(
            "formal resolver requires snapshot_class=FORMAL_CLOSE"
        )

    files = manifest.get("files") or {}
    model_ref = files.get("model")
    bonds_ref = files.get("bonds")
    if not model_ref or not bonds_ref:
        raise ResolverError("manifest files.model / files.bonds are required")

    contract_dir = manifest_path.parent
    model_path = contract_dir / str(model_ref)
    bonds_path = contract_dir / str(bonds_ref)

    model = _load_json(model_path, "model.json")
    if model.get("contract_version") != CONTRACT_VERSION:
        raise ResolverError("model contract_version mismatch")

    for key in ["snapshot_id", "snapshot_class", "market_cutoff", "model_version"]:
        if model.get(key) != manifest.get(key):
            raise ResolverError(f"manifest/model identity mismatch: {key}")

    if not bonds_path.exists():
        raise ResolverError(f"missing bonds.csv: {bonds_path}")
    try:
        bonds = pd.read_csv(
            bonds_path,
            dtype={"bond_code": str, "stock_code": str},
        )
    except Exception as exc:
        raise ResolverError(f"invalid bonds.csv: {bonds_path}") from exc

    if "bond_code" not in bonds.columns:
        raise ResolverError("bonds.csv missing bond_code")
    bonds["bond_code"] = bonds["bond_code"].astype(str).str.zfill(6)

    return manifest, model, bonds


def _model_parameters(model: dict[str, Any]) -> dict[str, float]:
    try:
        base = model["base"]
        duration = model["duration"]
        scale = model["scale_neutral"]
        calibration = model["calibration"]

        if base.get("model_type") != "huber_quadratic":
            raise ResolverError(
                f"unsupported base model_type={base.get('model_type')!r}"
            )
        if duration.get("model_type") != "huber_linear":
            raise ResolverError(
                f"unsupported duration model_type={duration.get('model_type')!r}"
            )
        if scale.get("model_type") != "huber_log_linear":
            raise ResolverError(
                f"unsupported scale model_type={scale.get('model_type')!r}"
            )

        bnorm = base["normalization"]
        dnorm = duration["normalization"]
        bp = base["params"]
        dp = duration["params"]
        sp = scale["params"]

        return {
            "base_center": _finite(bnorm["center"], "base.normalization.center"),
            "base_scale": _finite_positive(
                bnorm["scale"], "base.normalization.scale"
            ),
            "beta0": _finite(bp["beta0"], "base.params.beta0"),
            "beta1": _finite(bp["beta1"], "base.params.beta1"),
            "beta2": _finite(bp["beta2"], "base.params.beta2"),
            "duration_center": _finite(
                dnorm["center_months"], "duration.normalization.center_months"
            ),
            "duration_scale": _finite_positive(
                dnorm["scale_months"], "duration.normalization.scale_months"
            ),
            "duration_intercept": _finite(
                dp["intercept"], "duration.params.intercept"
            ),
            "duration_slope": _finite(dp["slope"], "duration.params.slope"),
            "scale_intercept": _finite(
                sp["intercept"], "scale_neutral.params.intercept"
            ),
            "scale_slope": _finite(
                sp["ln_size_slope"], "scale_neutral.params.ln_size_slope"
            ),
            "q50": _finite(calibration["q50"], "calibration.q50"),
        }
    except KeyError as exc:
        raise ResolverError(f"missing model parameter: {exc}") from exc


def resolve_bond_valuation(
    manifest_path: Path,
    *,
    bond_code: str,
    target_CV: float,
    target_remaining_months: float | None = None,
    target_remaining_size: float | None = None,
    scenario_id: str | None = None,
    require_formal: bool = True,
) -> dict[str, Any]:
    manifest, model, bonds = load_contract(
        Path(manifest_path),
        require_formal=require_formal,
    )

    code = str(bond_code).zfill(6)
    matched = bonds.loc[bonds["bond_code"].eq(code)]
    if len(matched) != 1:
        raise ResolverError(
            f"bond_code={code} must match exactly one row; matched={len(matched)}"
        )
    row = matched.iloc[0]

    target_cv = _finite_positive(target_CV, "target_CV")

    defaults_used: list[str] = []
    if target_remaining_months is None:
        target_months = _finite_positive(
            row["remaining_months"], "current remaining_months"
        )
        defaults_used.append("target_remaining_months")
    else:
        target_months = _finite_positive(
            target_remaining_months, "target_remaining_months"
        )

    if target_remaining_size is None:
        target_size = _finite_positive(
            row["remaining_size"], "current remaining_size"
        )
        defaults_used.append("target_remaining_size")
    else:
        target_size = _finite_positive(
            target_remaining_size, "target_remaining_size"
        )

    params = _model_parameters(model)

    z = (target_cv - params["base_center"]) / params["base_scale"]
    base_anchor = (
        params["beta0"]
        + params["beta1"] * z
        + params["beta2"] * z * z
    )
    duration_adjustment = (
        params["duration_intercept"]
        + params["duration_slope"]
        * (
            (target_months - params["duration_center"])
            / params["duration_scale"]
        )
    )
    scale_adjustment = (
        params["scale_intercept"]
        + params["scale_slope"] * math.log(target_size)
    )
    neutral_reference = (
        base_anchor + duration_adjustment + scale_adjustment
    )
    discovery_reference = neutral_reference + params["q50"]

    zones = model.get("zones") or {}
    core = zones.get("core")
    support = zones.get("support")
    if not (
        isinstance(core, list)
        and len(core) == 2
        and isinstance(support, list)
        and len(support) == 2
    ):
        raise ResolverError("model zones.core / zones.support are invalid")

    model_zone = classify_zone(target_cv, core, support)
    warnings: list[str] = []
    if model_zone.startswith("EXTRAPOLATED_"):
        warnings.append("MODEL_EXTRAPOLATION")

    current_price = _finite_positive(
        row["current_bond_price"], "current_bond_price"
    )
    current_cv = _finite_positive(
        row["current_conversion_value"], "current_conversion_value"
    )
    current_months = _finite_positive(
        row["remaining_months"], "current remaining_months"
    )
    current_size = _finite_positive(
        row["remaining_size"], "current remaining_size"
    )

    return {
        "resolver_version": RESOLVER_VERSION,
        "contract_version": manifest["contract_version"],
        "snapshot_id": manifest["snapshot_id"],
        "snapshot_class": manifest["snapshot_class"],
        "market_cutoff": manifest["market_cutoff"],
        "model_version": manifest["model_version"],
        "bond_code": code,
        "bond_name": str(row["bond_name"]),
        "scenario_id": scenario_id,
        "current_state": {
            "bond_price": current_price,
            "conversion_value": current_cv,
            "remaining_months": current_months,
            "remaining_size": current_size,
            "current_model_zone": str(row.get("model_zone") or ""),
        },
        "input": {
            "target_CV": target_cv,
            "target_remaining_months": target_months,
            "target_remaining_size": target_size,
            "defaults_used": defaults_used,
        },
        "valuation": {
            "base_anchor": base_anchor,
            "duration_adjustment": duration_adjustment,
            "scale_adjustment": scale_adjustment,
            "neutral_reference": neutral_reference,
            "calibration_q50": params["q50"],
            "discovery_reference": discovery_reference,
            "reference_minus_current_price": (
                discovery_reference - current_price
            ),
        },
        "model_position": {
            "model_zone": model_zone,
            "inside_core": model_zone == "CORE",
            "inside_support": model_zone in {"CORE", "SUPPORT"},
        },
        "warnings": warnings,
    }


def resolve_bond_scenarios(
    manifest_path: Path,
    *,
    bond_code: str,
    scenarios: list[dict[str, Any]],
    require_formal: bool = True,
) -> dict[str, Any]:
    if not scenarios:
        raise ResolverError("scenarios must contain at least one scenario")

    results: list[dict[str, Any]] = []
    identity: dict[str, Any] | None = None

    for index, scenario in enumerate(scenarios):
        if not isinstance(scenario, dict):
            raise ResolverError(f"scenario[{index}] must be an object")
        if "target_CV" not in scenario:
            raise ResolverError(f"scenario[{index}] missing target_CV")

        result = resolve_bond_valuation(
            manifest_path,
            bond_code=bond_code,
            target_CV=scenario["target_CV"],
            target_remaining_months=scenario.get("target_remaining_months"),
            target_remaining_size=scenario.get("target_remaining_size"),
            scenario_id=str(
                scenario.get("scenario_id")
                if scenario.get("scenario_id") is not None
                else f"scenario-{index + 1}"
            ),
            require_formal=require_formal,
        )
        if identity is None:
            identity = {
                "resolver_version": result["resolver_version"],
                "contract_version": result["contract_version"],
                "snapshot_id": result["snapshot_id"],
                "snapshot_class": result["snapshot_class"],
                "market_cutoff": result["market_cutoff"],
                "model_version": result["model_version"],
                "bond_code": result["bond_code"],
                "bond_name": result["bond_name"],
                "current_state": result["current_state"],
            }
        results.append(
            {
                "scenario_id": result["scenario_id"],
                "input": result["input"],
                "valuation": result["valuation"],
                "model_position": result["model_position"],
                "warnings": result["warnings"],
            }
        )

    assert identity is not None
    return {
        **identity,
        "scenario_count": len(results),
        "scenarios": results,
    }
