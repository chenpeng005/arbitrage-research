from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


CONTRACT_VERSION = "market-map-output-v1"
CONTRACT_DIRNAME = "market_map_output_v1"
VALIDATION_FILENAME = "output_contract_validation.json"

REQUIRED_BOND_COLUMNS = [
    "bond_code",
    "bond_name",
    "stock_code",
    "current_bond_price",
    "current_stock_price",
    "current_conversion_price",
    "current_conversion_value",
    "maturity_date",
    "remaining_months",
    "remaining_size",
    "issue_size",
    "base_anchor",
    "duration_adjustment",
    "scale_adjustment",
    "neutral_reference",
    "calibration_q50",
    "discovery_reference",
    "current_minus_reference",
    "reference_minus_current",
    "model_zone",
    "inside_core",
    "inside_support",
    "data_status",
    "warning_flags",
    "semantic_resolution_used",
    "source_manifest_ref",
]


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def classify_zone(cv: float, core: list[float], support: list[float]) -> str:
    core_min, core_max = map(float, core)
    support_min, support_max = map(float, support)
    if core_min <= cv <= core_max:
        return "CORE"
    if support_min <= cv <= support_max:
        return "SUPPORT"
    if cv < support_min:
        return "EXTRAPOLATED_LOW"
    return "EXTRAPOLATED_HIGH"


def _semantic_resolution_codes(input_dir: Path) -> set[str]:
    path = input_dir / "semantic_resolution.json"
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return set()

    codes: set[str] = set()
    for item in payload.get("resolutions", []) or []:
        if item.get("status") != "RESOLVED":
            continue
        code = str(item.get("bond_code") or "").zfill(6)
        if code:
            codes.add(code)
    return codes


def schema_payload() -> dict[str, Any]:
    def col(
        type_: str,
        required: bool,
        nullable: bool,
        unit: str | None,
        description: str,
    ) -> dict[str, Any]:
        return {
            "type": type_,
            "required": required,
            "nullable": nullable,
            "unit": unit,
            "description": description,
        }

    return {
        "contract_version": CONTRACT_VERSION,
        "table": "bonds.csv",
        "row_semantics": (
            "One row is one convertible bond's trusted current state and "
            "Market Map valuation explanation under this snapshot."
        ),
        "columns": {
            "bond_code": col("string", True, False, None, "可转债代码，保留6位字符串。"),
            "bond_name": col("string", True, False, None, "可转债正式完整名称。"),
            "stock_code": col("string", True, False, None, "正股代码。"),
            "current_bond_price": col("number", True, False, "CNY", "当前可信转债价格。"),
            "current_stock_price": col("number", True, False, "CNY", "当前 trusted_S。"),
            "current_conversion_price": col(
                "number", True, False, "CNY/share", "当前 trusted_K。"
            ),
            "current_conversion_value": col(
                "number",
                True,
                False,
                "CNY per 100 face",
                "100 × trusted_S / trusted_K；正式模型 CV。",
            ),
            "source_conversion_value": col(
                "number",
                False,
                True,
                "CNY per 100 face",
                "外部来源原始 CV，仅用于审计。",
            ),
            "maturity_date": col("date", True, False, None, "当前认可的合同到期日。"),
            "remaining_months": col(
                "number",
                True,
                False,
                "month",
                "按 Runtime DAYS_PER_MONTH 规则折算的剩余期限。",
            ),
            "remaining_size": col("number", True, False, "亿元", "当前剩余规模。"),
            "issue_size": col("number", True, False, "亿元", "原始发行规模。"),
            "base_anchor": col("number", True, False, "CNY", "BaseAnchor(CV)。"),
            "duration_adjustment": col(
                "number", True, False, "CNY", "DurationAdjustment(T)。"
            ),
            "scale_adjustment": col(
                "number", True, False, "CNY", "ScaleNeutral(Size)。"
            ),
            "neutral_reference": col(
                "number",
                True,
                False,
                "CNY",
                "Base + Duration + ScaleNeutral。",
            ),
            "calibration_q50": col(
                "number",
                True,
                False,
                "CNY",
                "当日 Core Zone 最终 residual Q50 校准。",
            ),
            "discovery_reference": col(
                "number",
                True,
                False,
                "CNY",
                "NeutralReference + Q50。",
            ),
            "current_minus_reference": col(
                "number",
                True,
                False,
                "CNY",
                "CurrentPrice - DiscoveryReference。",
            ),
            "reference_minus_current": col(
                "number",
                True,
                False,
                "CNY",
                "DiscoveryReference - CurrentPrice。",
            ),
            "model_zone": col(
                "enum",
                True,
                False,
                None,
                "CORE / SUPPORT / EXTRAPOLATED_LOW / EXTRAPOLATED_HIGH。",
            ),
            "inside_core": col("boolean", True, False, None, "CV 是否处于 Core Zone。"),
            "inside_support": col(
                "boolean", True, False, None, "CV 是否处于 Support Zone。"
            ),
            "data_status": col("string", True, False, None, "上游可信输入状态。"),
            "warning_flags": col(
                "string", True, True, None, "上游数据 / 语义审计 warning flags。"
            ),
            "semantic_resolution_used": col(
                "boolean",
                True,
                False,
                None,
                "本券是否使用 Program 验证通过的 AI Semantic Resolution。",
            ),
            "source_manifest_ref": col(
                "string", True, False, None, "上游 Source Manifest 引用。"
            ),
            "semantic_evidence_ref": col(
                "string",
                False,
                True,
                None,
                "若本券使用 Semantic Resolution，则指向证据 Manifest。",
            ),
        },
    }


def build_bonds(
    snapshot: dict[str, Any],
    model_df: pd.DataFrame,
) -> pd.DataFrame:
    zones = snapshot["zones"]
    core = zones["core"]
    support = zones["support"]
    q50 = float(snapshot["discovery_reference"]["q50_calibration"])

    input_dir = Path(snapshot["input"]["input_dir"])
    resolution_codes = _semantic_resolution_codes(input_dir)
    semantic_evidence_path = input_dir / "semantic_evidence_manifest.json"
    semantic_evidence_ref = (
        str(semantic_evidence_path) if semantic_evidence_path.exists() else ""
    )
    source_manifest_ref = str(snapshot["input"].get("source_manifest_ref") or "")

    df = model_df.copy()
    df["bond_code"] = df["bond_code"].astype(str).str.zfill(6)
    if "stock_code" in df.columns:
        df["stock_code"] = df["stock_code"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)

    out = pd.DataFrame()
    out["bond_code"] = df["bond_code"]
    out["bond_name"] = df["bond_name"].astype(str)
    out["stock_code"] = df["stock_code"].astype(str)
    out["current_bond_price"] = pd.to_numeric(df["P"], errors="coerce")
    out["current_stock_price"] = pd.to_numeric(df["trusted_S"], errors="coerce")
    out["current_conversion_price"] = pd.to_numeric(df["trusted_K"], errors="coerce")
    out["current_conversion_value"] = pd.to_numeric(df["trusted_CV"], errors="coerce")
    out["source_conversion_value"] = pd.to_numeric(df["source_CV"], errors="coerce")
    out["maturity_date"] = pd.to_datetime(df["maturity_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    out["remaining_months"] = pd.to_numeric(df["remaining_months"], errors="coerce")
    out["remaining_size"] = pd.to_numeric(df["remaining_size"], errors="coerce")
    out["issue_size"] = pd.to_numeric(df["issue_size"], errors="coerce")
    out["base_anchor"] = pd.to_numeric(df["base_anchor"], errors="coerce")
    out["duration_adjustment"] = pd.to_numeric(df["duration_adjustment"], errors="coerce")
    out["scale_adjustment"] = pd.to_numeric(df["scale_neutral"], errors="coerce")
    out["neutral_reference"] = pd.to_numeric(df["anchor_neutral"], errors="coerce")
    out["calibration_q50"] = q50
    out["discovery_reference"] = pd.to_numeric(df["discovery_reference"], errors="coerce")
    out["current_minus_reference"] = (
        out["current_bond_price"] - out["discovery_reference"]
    )
    out["reference_minus_current"] = -out["current_minus_reference"]

    out["model_zone"] = out["current_conversion_value"].map(
        lambda x: classify_zone(float(x), core, support) if pd.notna(x) else ""
    )
    out["inside_core"] = out["model_zone"].eq("CORE")
    out["inside_support"] = out["model_zone"].isin(["CORE", "SUPPORT"])
    out["data_status"] = df["data_status"].astype(str)
    out["warning_flags"] = df["warning_flags"].fillna("").astype(str)
    out["semantic_resolution_used"] = out["bond_code"].isin(resolution_codes)
    out["source_manifest_ref"] = source_manifest_ref
    out["semantic_evidence_ref"] = out.apply(
        lambda r: semantic_evidence_ref if bool(r["semantic_resolution_used"]) else "",
        axis=1,
    )
    return out


def build_model(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": snapshot["snapshot_id"],
        "market_cutoff": snapshot["market_cutoff"],
        "snapshot_class": snapshot["snapshot_class"],
        "model_version": snapshot["model_version"],
        "cv_field": snapshot.get("cv_field", "trusted_CV"),
        "zones": snapshot["zones"],
        "base": {
            **snapshot["components"]["base"],
            "model_type": "huber_quadratic",
            "normalization": {"center": 90.0, "scale": 20.0},
        },
        "duration": {
            **snapshot["components"]["duration"],
            "model_type": "huber_linear",
            "normalization": {"center_months": 36.0, "scale_months": 24.0},
        },
        "scale_neutral": {
            **snapshot["components"]["scale_neutral"],
            "model_type": "huber_log_linear",
            "input_transform": "ln(remaining_size)",
        },
        "scale_conservative": snapshot["components"].get("scale_conservative"),
        "calibration": {
            "type": "core_residual_q50",
            "q50": snapshot["discovery_reference"]["q50_calibration"],
            "formula": snapshot["discovery_reference"]["formula"],
        },
        "diagnostics": snapshot.get("diagnostics", {}),
        "model_audit_ref": snapshot.get("model_audit_ref"),
    }


def build_manifest(
    snapshot: dict[str, Any],
    bonds: pd.DataFrame,
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_class": snapshot["snapshot_class"],
        "market_cutoff": snapshot["market_cutoff"],
        "model_version": snapshot["model_version"],
        "created_at": now_utc(),
        "bond_count": int(len(bonds)),
        "files": {
            "schema": "schema.json",
            "model": "model.json",
            "bonds": "bonds.csv",
        },
        "upstream": {
            "acquisition_run_id": snapshot["input"].get("acquisition_run_id"),
            "calculation_snapshot_ref": "../market_map_snapshot.json",
            "trusted_market_input_ref": snapshot["input"].get("trusted_market_input_ref"),
            "model_audit_ref": "../model_audit.json",
            "source_manifest_ref": snapshot["input"].get("source_manifest_ref"),
            "semantic_resolution_status": snapshot["input"].get(
                "semantic_resolution_status"
            ),
        },
        "validation": {
            "status": "PENDING",
            "validated_at": None,
            "validation_ref": f"../{VALIDATION_FILENAME}",
        },
    }


def _max_abs(series: pd.Series) -> float:
    values = pd.to_numeric(series, errors="coerce").abs()
    return float(values.max()) if len(values) else 0.0


def validate_contract(
    output_dir: Path,
    *,
    tolerance: float = 1e-6,
) -> dict[str, Any]:
    contract_dir = output_dir / CONTRACT_DIRNAME
    manifest_path = contract_dir / "manifest.json"
    schema_path = contract_dir / "schema.json"
    model_path = contract_dir / "model.json"
    bonds_path = contract_dir / "bonds.csv"

    errors: list[str] = []
    warnings: list[str] = []

    for path in [manifest_path, schema_path, model_path, bonds_path]:
        if not path.exists():
            errors.append(f"missing contract file: {path.name}")

    manifest: dict[str, Any] = {}
    schema: dict[str, Any] = {}
    model: dict[str, Any] = {}
    bonds = pd.DataFrame()

    if not errors:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            model = json.loads(model_path.read_text(encoding="utf-8"))
            bonds = pd.read_csv(
                bonds_path,
                dtype={"bond_code": str, "stock_code": str},
            )
        except Exception as exc:
            errors.append(f"contract parse failed: {type(exc).__name__}: {exc}")

    if not errors:
        if manifest.get("contract_version") != CONTRACT_VERSION:
            errors.append("manifest contract_version mismatch")
        if schema.get("contract_version") != CONTRACT_VERSION:
            errors.append("schema contract_version mismatch")
        if model.get("contract_version") != CONTRACT_VERSION:
            errors.append("model contract_version mismatch")

        identity_fields = ["snapshot_id", "market_cutoff", "model_version"]
        for key in identity_fields:
            if manifest.get(key) != model.get(key):
                errors.append(f"manifest/model identity mismatch: {key}")

        missing_cols = [c for c in REQUIRED_BOND_COLUMNS if c not in bonds.columns]
        if missing_cols:
            errors.append("missing required bonds columns: " + ", ".join(missing_cols))

        if int(manifest.get("bond_count") or -1) != len(bonds):
            errors.append("manifest bond_count mismatch")

        if "bond_code" in bonds and bonds["bond_code"].duplicated().any():
            errors.append("duplicate bond_code in bonds.csv")

    if not errors and len(bonds):
        numeric_positive = [
            "current_bond_price",
            "current_stock_price",
            "current_conversion_price",
            "current_conversion_value",
            "remaining_months",
            "remaining_size",
            "issue_size",
        ]
        numeric_finite = [
            *numeric_positive,
            "source_conversion_value",
            "base_anchor",
            "duration_adjustment",
            "scale_adjustment",
            "neutral_reference",
            "calibration_q50",
            "discovery_reference",
            "current_minus_reference",
            "reference_minus_current",
        ]

        for col in numeric_finite:
            values = pd.to_numeric(bonds[col], errors="coerce")
            if col == "source_conversion_value":
                invalid = values.notna() & ~values.map(math.isfinite)
            else:
                invalid = values.isna() | ~values.map(math.isfinite)
            if bool(invalid.any()):
                errors.append(f"non-finite numeric values: {col}")

        for col in numeric_positive:
            values = pd.to_numeric(bonds[col], errors="coerce")
            if bool((values <= 0).any()):
                errors.append(f"non-positive required values: {col}")

        cv_recalc = (
            100.0
            * pd.to_numeric(bonds["current_stock_price"])
            / pd.to_numeric(bonds["current_conversion_price"])
        )
        if _max_abs(cv_recalc - pd.to_numeric(bonds["current_conversion_value"])) > tolerance:
            errors.append("current_conversion_value formula mismatch")

        neutral_recalc = (
            pd.to_numeric(bonds["base_anchor"])
            + pd.to_numeric(bonds["duration_adjustment"])
            + pd.to_numeric(bonds["scale_adjustment"])
        )
        if _max_abs(neutral_recalc - pd.to_numeric(bonds["neutral_reference"])) > tolerance:
            errors.append("neutral_reference decomposition mismatch")

        discovery_recalc = (
            pd.to_numeric(bonds["neutral_reference"])
            + pd.to_numeric(bonds["calibration_q50"])
        )
        if _max_abs(discovery_recalc - pd.to_numeric(bonds["discovery_reference"])) > tolerance:
            errors.append("discovery_reference calibration mismatch")

        diff_sum = (
            pd.to_numeric(bonds["current_minus_reference"])
            + pd.to_numeric(bonds["reference_minus_current"])
        )
        if _max_abs(diff_sum) > tolerance:
            errors.append("diff fields are not exact opposites")

        core = model["zones"]["core"]
        support = model["zones"]["support"]
        expected_zone = bonds["current_conversion_value"].map(
            lambda x: classify_zone(float(x), core, support)
        )
        if bool((expected_zone != bonds["model_zone"]).any()):
            errors.append("model_zone classification mismatch")

        expected_core = expected_zone.eq("CORE")
        expected_support = expected_zone.isin(["CORE", "SUPPORT"])
        actual_core = bonds["inside_core"].astype(str).str.lower().eq("true")
        actual_support = bonds["inside_support"].astype(str).str.lower().eq("true")
        if bool((expected_core != actual_core).any()):
            errors.append("inside_core mismatch")
        if bool((expected_support != actual_support).any()):
            errors.append("inside_support mismatch")

    status = "PASS" if not errors else "FAIL"
    validation = {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "validated_at": now_utc(),
        "bond_count": int(len(bonds)),
        "errors": errors,
        "warnings": warnings,
        "contract_dir": str(contract_dir),
        "manifest_path": str(manifest_path),
    }
    write_json(output_dir / VALIDATION_FILENAME, validation)

    if manifest_path.exists():
        try:
            if not manifest:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["validation"] = {
                "status": status,
                "validated_at": validation["validated_at"],
                "validation_ref": f"../{VALIDATION_FILENAME}",
            }
            write_json(manifest_path, manifest)
        except Exception:
            pass

    return validation


def build_and_validate_output_contract(
    output_dir: Path,
    snapshot: dict[str, Any],
    model_df: pd.DataFrame,
) -> dict[str, Any]:
    contract_dir = output_dir / CONTRACT_DIRNAME
    contract_dir.mkdir(parents=True, exist_ok=True)

    bonds = build_bonds(snapshot, model_df)
    write_json(contract_dir / "schema.json", schema_payload())
    write_json(contract_dir / "model.json", build_model(snapshot))
    bonds.to_csv(contract_dir / "bonds.csv", index=False)

    manifest = build_manifest(snapshot, bonds)
    write_json(contract_dir / "manifest.json", manifest)

    validation = validate_contract(output_dir)
    if validation["status"] != "PASS":
        raise RuntimeError(
            "Market Map Output Contract validation failed: "
            + "; ".join(validation.get("errors") or [])
        )

    return {
        "contract_version": CONTRACT_VERSION,
        "status": "PASS",
        "contract_dir": str(contract_dir),
        "manifest_path": str(contract_dir / "manifest.json"),
        "validation_path": str(output_dir / VALIDATION_FILENAME),
        "bond_count": int(len(bonds)),
    }
