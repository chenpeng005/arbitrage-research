from __future__ import annotations

import argparse
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

DAYS_PER_MONTH = 365.2425 / 12.0
ALLOWED_STATUS = {"RESOLVED", "INSUFFICIENT_EVIDENCE", "NOT_APPLICABLE"}


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fail_validation(
    run_dir: Path,
    request: dict[str, Any] | None,
    errors: list[str],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "status": "FAIL",
        "validated_at": now_utc(),
        "request_run_id": (request or {}).get("run_id"),
        "market_cutoff": (request or {}).get("market_cutoff"),
        "resolved_count": 0,
        "required_count": len((request or {}).get("requests", [])),
        "errors": errors,
        "warnings": warnings or [],
        "trusted_market_input_generated": False,
    }
    write_json(run_dir / "semantic_resolution_validation.json", payload)
    trusted_path = run_dir / "trusted_market_input.csv"
    if trusted_path.exists():
        trusted_path.unlink()
    return payload


def validate_resolution(
    run_dir: Path,
    resolution_file: Path,
) -> dict[str, Any]:
    request_path = run_dir / "semantic_review_request.json"
    audit_path = run_dir / "acquisition_audit.json"
    working_path = run_dir / "market_input_audit.csv"

    missing = [
        str(p.name)
        for p in [request_path, audit_path, working_path, resolution_file]
        if not p.exists()
    ]
    if missing:
        return fail_validation(
            run_dir,
            None,
            [f"缺少必要文件：{', '.join(missing)}"],
        )

    request = load_json(request_path)
    resolution = load_json(resolution_file)

    evidence_manifest_path = run_dir / "semantic_evidence_manifest.json"
    evidence_manifest = {}
    evidence_map: dict[str, dict[str, Any]] = {}
    if evidence_manifest_path.exists():
        evidence_manifest = load_json(evidence_manifest_path)
        evidence_map = {
            str(item.get("evidence_id")): item
            for item in evidence_manifest.get("evidence", [])
            if item.get("evidence_id")
        }

    errors: list[str] = []
    warnings: list[str] = []

    if resolution.get("request_run_id") != request.get("run_id"):
        errors.append("request_run_id 与 semantic_review_request.json 不一致。")
    if resolution.get("market_cutoff") != request.get("market_cutoff"):
        errors.append("market_cutoff 与 semantic_review_request.json 不一致。")

    req_items = request.get("requests", [])
    res_items = resolution.get("resolutions", [])
    req_map = {x.get("conflict_id"): x for x in req_items}
    res_map: dict[str, dict[str, Any]] = {}

    if len(req_map) != len(req_items):
        errors.append("semantic_review_request.json 存在重复 conflict_id。")

    for item in res_items:
        conflict_id = item.get("conflict_id")
        if not conflict_id:
            errors.append("Resolution 缺少 conflict_id。")
            continue
        if conflict_id in res_map:
            errors.append(f"Resolution 重复 conflict_id：{conflict_id}")
            continue
        res_map[conflict_id] = item
        if conflict_id not in req_map:
            errors.append(f"Resolution 包含未请求的 conflict_id：{conflict_id}")

    missing_ids = [cid for cid in req_map if cid not in res_map]
    if missing_ids:
        errors.append(
            "Resolution 未覆盖全部冲突：" + ", ".join(missing_ids)
        )

    cutoff = pd.Timestamp(request.get("market_cutoff"))

    for conflict_id, req in req_map.items():
        item = res_map.get(conflict_id)
        if item is None:
            continue

        if str(item.get("bond_code")) != str(req.get("bond_code")):
            errors.append(f"{conflict_id}: bond_code 与请求不一致。")
        if item.get("field") != req.get("field"):
            errors.append(f"{conflict_id}: field 与请求不一致。")

        status = item.get("status")
        if status not in ALLOWED_STATUS:
            errors.append(f"{conflict_id}: 非法 status={status!r}。")
            continue

        if status != "RESOLVED":
            errors.append(
                f"{conflict_id}: status={status}，仍未形成可进入 Trusted Input 的确定结论。"
            )
            continue

        evidence = item.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            errors.append(f"{conflict_id}: RESOLVED 但缺少 evidence。")
        elif evidence_map:
            for ev in evidence:
                evidence_id = str(ev.get("evidence_id") or "")
                if not evidence_id:
                    errors.append(
                        f"{conflict_id}: AI Runtime evidence 缺少 evidence_id。"
                    )
                    continue
                manifest_item = evidence_map.get(evidence_id)
                if manifest_item is None:
                    errors.append(
                        f"{conflict_id}: evidence_id={evidence_id} 不在 Program 抓取的证据 Manifest 中。"
                    )
                    continue

                locator = str(ev.get("locator") or "")
                valid_locators = {
                    str(manifest_item.get("pdf_url") or ""),
                    str(manifest_item.get("detail_url") or ""),
                }
                valid_locators.discard("")
                if locator and locator not in valid_locators:
                    errors.append(
                        f"{conflict_id}: evidence_id={evidence_id} 的 locator 与 Program 证据不一致。"
                    )

                source_type = str(ev.get("source_type") or "")
                manifest_source = str(manifest_item.get("source_type") or "")
                if source_type and source_type != manifest_source:
                    errors.append(
                        f"{conflict_id}: evidence_id={evidence_id} 的 source_type 与 Program 证据不一致。"
                    )

        field = req.get("field")
        value = item.get("resolved_value")

        if field == "K":
            try:
                number = float(value)
                if not math.isfinite(number) or number <= 0:
                    raise ValueError
            except Exception:
                errors.append(f"{conflict_id}: resolved K 必须是有限正数。")

            effective_from = item.get("effective_from")
            if effective_from:
                try:
                    effective_ts = pd.Timestamp(effective_from)
                    if effective_ts > cutoff:
                        errors.append(
                            f"{conflict_id}: effective_from 晚于 market_cutoff。"
                        )
                except Exception:
                    errors.append(f"{conflict_id}: effective_from 不是合法日期。")

        elif field == "maturity_date":
            try:
                maturity = pd.Timestamp(value)
                if pd.isna(maturity):
                    raise ValueError
            except Exception:
                errors.append(
                    f"{conflict_id}: resolved maturity_date 必须是合法日期。"
                )
        else:
            errors.append(
                f"{conflict_id}: 当前 Program Apply 尚不支持 field={field!r}。"
            )

    if errors:
        return fail_validation(run_dir, request, errors, warnings)

    # Persist a normalized copy of the accepted AI result. Raw/Audit remain untouched.
    normalized_resolution_path = run_dir / "semantic_resolution.json"
    if resolution_file.resolve() != normalized_resolution_path.resolve():
        shutil.copy2(resolution_file, normalized_resolution_path)

    audit_df = pd.read_csv(
        working_path,
        dtype={"bond_code": str, "stock_code": str},
    )
    audit_df["bond_code"] = audit_df["bond_code"].astype(str).str.zfill(6)

    # Start from deterministic primary values. Semantic resolutions only touch
    # the exact requested field for the exact requested bond.
    trusted = audit_df.copy()
    trusted["trusted_S"] = pd.to_numeric(trusted["S"], errors="coerce")
    trusted["trusted_K"] = pd.to_numeric(trusted["K"], errors="coerce")
    trusted["trusted_maturity_date"] = pd.to_datetime(
        trusted["maturity_date"], errors="coerce"
    )

    applied_fields: dict[str, list[str]] = {}

    for conflict_id, req in req_map.items():
        item = res_map[conflict_id]
        code = str(req["bond_code"]).zfill(6)
        mask = trusted["bond_code"].eq(code)
        if int(mask.sum()) != 1:
            return fail_validation(
                run_dir,
                request,
                [f"{conflict_id}: bond_code={code} 在审计工作表中不是唯一一行。"],
            )

        field = req["field"]
        if field == "K":
            trusted.loc[mask, "trusted_K"] = float(item["resolved_value"])
        elif field == "maturity_date":
            trusted.loc[mask, "trusted_maturity_date"] = pd.Timestamp(
                item["resolved_value"]
            )

        applied_fields.setdefault(code, []).append(field)

    trusted["trusted_CV"] = (
        100.0 * trusted["trusted_S"] / trusted["trusted_K"]
    )
    trusted["maturity_date"] = trusted["trusted_maturity_date"]
    trusted["remaining_months"] = (
        trusted["maturity_date"] - cutoff
    ).dt.days / DAYS_PER_MONTH

    invalid_trusted = (
        trusted["P"].isna()
        | trusted["trusted_S"].isna()
        | trusted["trusted_K"].isna()
        | trusted["trusted_CV"].isna()
        | trusted["maturity_date"].isna()
        | trusted["remaining_size"].isna()
        | (pd.to_numeric(trusted["P"], errors="coerce") <= 0)
        | (trusted["trusted_S"] <= 0)
        | (trusted["trusted_K"] <= 0)
        | (trusted["trusted_CV"] <= 0)
        | (trusted["remaining_months"] <= 0)
        | (pd.to_numeric(trusted["remaining_size"], errors="coerce") <= 0)
    )
    if bool(invalid_trusted.any()):
        bad_codes = trusted.loc[invalid_trusted, "bond_code"].astype(str).tolist()
        return fail_validation(
            run_dir,
            request,
            ["Resolution Apply 后仍存在无效 Trusted 字段：" + ", ".join(bad_codes[:20])],
        )

    def warning_flags(row: pd.Series) -> str:
        flags: list[str] = []

        code = str(row["bond_code"]).zfill(6)
        for field in applied_fields.get(code, []):
            flags.append(f"SEMANTIC_RESOLUTION_{field.upper()}")

        maturity_diff = row.get("maturity_day_diff")
        if (
            "maturity_date" not in applied_fields.get(code, [])
            and pd.notna(maturity_diff)
            and 0 < abs(float(maturity_diff)) <= 1
        ):
            flags.append("MATURITY_CONVENTION_DIFF")

        p_diff = row.get("P_aux_diff")
        if pd.notna(p_diff) and float(p_diff) > 1e-9:
            flags.append("P_CROSS_SOURCE_DIFF")

        s_diff = row.get("S_aux_diff")
        if pd.notna(s_diff) and float(s_diff) > 1e-9:
            flags.append("S_CROSS_SOURCE_DIFF")

        aux_name = row.get("size_source_name")
        if (
            pd.notna(aux_name)
            and str(row.get("bond_name", "")).strip() != str(aux_name).strip()
        ):
            flags.append("IDENTITY_NAME_DIFF")

        return ";".join(flags)

    trusted["warning_flags"] = trusted.apply(warning_flags, axis=1)
    trusted["data_status"] = "TRUSTED_RESOLVED"
    trusted["source_manifest_ref"] = "source_manifest.json"
    trusted["semantic_resolution_ref"] = "semantic_resolution.json"
    trusted["semantic_validation_ref"] = "semantic_resolution_validation.json"
    trusted["semantic_evidence_manifest_ref"] = (
        "semantic_evidence_manifest.json" if evidence_manifest_path.exists() else ""
    )

    output_cols = [
        "bond_code",
        "bond_name",
        "stock_code",
        "P",
        "S",
        "K",
        "trusted_S",
        "trusted_K",
        "source_CV",
        "calc_CV",
        "trusted_CV",
        "maturity_date",
        "remaining_months",
        "remaining_size",
        "issue_size",
        "data_status",
        "warning_flags",
        "source_manifest_ref",
        "semantic_resolution_ref",
        "semantic_validation_ref",
        "semantic_evidence_manifest_ref",
    ]
    missing_cols = [col for col in output_cols if col not in trusted.columns]
    if missing_cols:
        return fail_validation(
            run_dir,
            request,
            ["无法生成 Trusted Input，缺少字段：" + ", ".join(missing_cols)],
        )

    trusted_path = run_dir / "trusted_market_input.csv"
    trusted[output_cols].to_csv(trusted_path, index=False)

    validation = {
        "status": "PASS",
        "validated_at": now_utc(),
        "request_run_id": request.get("run_id"),
        "market_cutoff": request.get("market_cutoff"),
        "resolver_type": resolution.get("resolver_type"),
        "required_count": len(req_items),
        "resolved_count": len(req_items),
        "applied_fields": applied_fields,
        "errors": [],
        "warnings": warnings,
        "trusted_market_input_generated": True,
        "trusted_market_input_ref": "trusted_market_input.csv",
        "semantic_resolution_ref": "semantic_resolution.json",
        "semantic_evidence_manifest_ref": (
            "semantic_evidence_manifest.json"
            if evidence_manifest_path.exists()
            else None
        ),
        "evidence_count": len(evidence_map),
        "cv_field": "trusted_CV",
    }
    write_json(run_dir / "semantic_resolution_validation.json", validation)
    return validation


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--resolution-file", required=True)
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve()
    resolution_file = Path(args.resolution_file).resolve()

    result = validate_resolution(run_dir, resolution_file)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if result.get("status") != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
