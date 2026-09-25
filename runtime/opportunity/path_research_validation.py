"""Validation for AI PATH_RESEARCH structured output."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ALLOWED_STATUS = {"COMPLETED", "NEEDS_EVIDENCE", "UNRESOLVED"}
ALLOWED_LOGIC_STATES = {
    "KNOWN", "DERIVED", "MIXED", "NOT_MATERIAL", "UNKNOWN_A", "UNKNOWN_B"
}
PATH_LOGIC_STEPS = {
    "MATURITY_CASH": [
        "M1_CONTRACT_CASH",
        "M2_PATH_EXISTS",
        "M3_LIQUIDITY",
        "M4_COMPETING_CASH",
        "M5_INTERNAL_CASH",
        "M6_EXTERNAL_CLOSURE",
        "M7_HARD_CREDIT",
        "M8_PAYMENT_STABILITY",
    ],
    "PUT": [
        "P1_LEGAL_TIME",
        "P2_TRIGGER_STATE",
        "P3_REVISION_INTERACTION",
        "P4_CONTRACT_CASH",
        "P5_EXERCISE_PRESSURE",
        "P6_PAYMENT_STABILITY",
        "P7_PATH_JUDGMENT",
    ],
    "DOWNWARD_REVISION": [
        "R1_CURRENT_START",
        "R2_EXECUTABLE_SPACE",
        "R3_BEHAVIOR_HISTORY",
        "R4_ISSUER_OBJECTIVE",
        "R5_RULE_BOUNDARY",
        "R6_REALISTIC_RESULT",
        "R7_PRICE_TRANSLATION",
        "R8_PATH_JUDGMENT",
    ],
}
REQUIRED_FIELDS = (
    "path_result_id",
    "task_id",
    "trigger_key",
    "bond_code",
    "bond_name",
    "path_id",
    "research_cutoff",
    "path_research_canonical_path",
    "knowledge_commit_sha",
    "review_ready",
    "research_status",
    "summary",
    "logic_chain",
    "fact_spine",
    "judgments",
    "key_evidence",
    "unknown_a",
    "unknown_b",
    "key_risks",
    "failure_conditions",
    "next_update_nodes",
    "economic_status_at_research",
    "economic_judgment_reference",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _fail(run_dir: Path, errors: list[str], warnings: list[str] | None = None) -> dict[str, Any]:
    payload = {
        "status": "FAIL",
        "validated_at": _now(),
        "errors": errors,
        "warnings": warnings or [],
    }
    _write_json(run_dir / "path_research_validation.json", payload)
    return payload


def validate_path_research_result(
    run_dir: Path,
    result_file: Path,
) -> dict[str, Any]:
    input_path = run_dir / "ai_input.json"
    if not input_path.exists() or not result_file.exists():
        missing = [
            p.name for p in (input_path, result_file) if not p.exists()
        ]
        return _fail(run_dir, [f"missing required file(s): {', '.join(missing)}"])

    request = _read_json(input_path)
    result = _read_json(result_file)
    task = request.get("path_research_task") or {}
    errors: list[str] = []
    warnings: list[str] = []

    missing = [key for key in REQUIRED_FIELDS if key not in result]
    if missing:
        errors.append("Path Result missing fields: " + ", ".join(missing))

    identity_pairs = [
        ("task_id", task.get("task_id")),
        ("trigger_key", task.get("trigger_key")),
        ("bond_code", str(task.get("bond_code") or "").zfill(6)),
        ("bond_name", task.get("bond_name")),
        ("path_id", task.get("path_id")),
        ("path_research_canonical_path", task.get("path_research_canonical_path")),
        ("knowledge_commit_sha", task.get("knowledge_commit_sha")),
    ]
    for key, expected in identity_pairs:
        actual = result.get(key)
        if key == "bond_code" and actual is not None:
            actual = str(actual).zfill(6)
        if actual != expected:
            errors.append(f"{key} mismatch: expected={expected!r}, actual={actual!r}")

    expected_result_id = request.get("expected_path_result_id")
    if result.get("path_result_id") != expected_result_id:
        errors.append("path_result_id does not match expected deterministic id")

    cutoff = str(request.get("research_cutoff") or "")
    if str(result.get("research_cutoff") or "") != cutoff:
        errors.append("research_cutoff mismatch")

    if result.get("economic_status_at_research") != "KEEP":
        errors.append("economic_status_at_research must remain KEEP")

    status = result.get("research_status")
    if status not in ALLOWED_STATUS:
        errors.append(f"invalid research_status={status!r}")

    review_ready = result.get("review_ready")
    if not isinstance(review_ready, bool):
        errors.append("review_ready must be boolean")
    elif review_ready and status != "COMPLETED":
        errors.append("review_ready=true requires research_status=COMPLETED")

    unknown_a = result.get("unknown_a")
    unknown_b = result.get("unknown_b")
    for key, value in (("unknown_a", unknown_a), ("unknown_b", unknown_b)):
        if not isinstance(value, list):
            errors.append(f"{key} must be a list")

    if review_ready and isinstance(unknown_b, list) and unknown_b:
        errors.append("review_ready=true cannot contain material UNKNOWN-B")

    for key in ("fact_spine", "judgments", "economic_judgment_reference"):
        if not isinstance(result.get(key), dict):
            errors.append(f"{key} must be an object")

    summary = result.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary must be an object")
    else:
        for key in ("core_conclusion", "economic_result", "next_focus"):
            if not isinstance(summary.get(key), str) or not summary.get(key, "").strip():
                errors.append(f"summary.{key} must be a non-empty string")
        for key in ("why", "main_risks"):
            if not isinstance(summary.get(key), list):
                errors.append(f"summary.{key} must be a list")
            elif review_ready and not summary.get(key):
                errors.append(f"review_ready summary.{key} cannot be empty")

    logic_chain = result.get("logic_chain")
    logic_step_ids: list[str] = []
    if not isinstance(logic_chain, list):
        errors.append("logic_chain must be a list")
        logic_chain = []
    else:
        for idx, step in enumerate(logic_chain):
            if not isinstance(step, dict):
                errors.append(f"logic_chain[{idx}] must be an object")
                continue
            step_id = str(step.get("step_id") or "")
            logic_step_ids.append(step_id)
            for key in ("step_id", "title", "question", "answer", "reasoning", "conclusion"):
                if not isinstance(step.get(key), str) or not str(step.get(key) or "").strip():
                    errors.append(f"logic_chain[{idx}].{key} must be a non-empty string")
            if step.get("state") not in ALLOWED_LOGIC_STATES:
                errors.append(
                    f"logic_chain[{idx}].state invalid: {step.get('state')!r}"
                )
            for key in ("facts", "evidence_ids"):
                if not isinstance(step.get(key), list):
                    errors.append(f"logic_chain[{idx}].{key} must be a list")
            if (
                review_ready
                and step.get("state") != "NOT_MATERIAL"
                and isinstance(step.get("facts"), list)
                and not step.get("facts")
            ):
                errors.append(
                    f"review_ready logic_chain[{idx}] must contain concrete facts "
                    "unless state=NOT_MATERIAL"
                )
            if review_ready and step.get("state") == "UNKNOWN_B":
                errors.append(
                    f"review_ready logic_chain[{idx}] cannot remain UNKNOWN_B"
                )

    if len(logic_step_ids) != len(set(logic_step_ids)):
        errors.append("logic_chain step_id must be unique")

    expected_steps = PATH_LOGIC_STEPS.get(str(task.get("path_id") or ""), [])
    if review_ready:
        if logic_step_ids != expected_steps:
            errors.append(
                "review_ready logic_chain must exactly follow required path order: "
                + " -> ".join(expected_steps)
            )
    elif logic_step_ids:
        unknown_steps = [x for x in logic_step_ids if x not in expected_steps]
        if unknown_steps:
            errors.append(
                "logic_chain contains unsupported step_id(s): "
                + ", ".join(unknown_steps)
            )

    logic_by_id = {
        str(step.get("step_id")): step
        for step in logic_chain
        if isinstance(step, dict) and step.get("step_id")
    }
    path_id = str(task.get("path_id") or "")
    if review_ready and path_id == "PUT":
        p5 = logic_by_id.get("P5_EXERCISE_PRESSURE") or {}
        p5_ids = [str(x) for x in (p5.get("evidence_ids") or [])]
        if "PRELOADED:PUT_PRESSURE_SCENARIOS" not in p5_ids:
            errors.append(
                "PUT P5_EXERCISE_PRESSURE must cite "
                "PRELOADED:PUT_PRESSURE_SCENARIOS"
            )
        pressure_text = " ".join(str(x) for x in (p5.get("facts") or []))
        scenario_aliases = {
            "30%": ("30%", "0.3"),
            "50%": ("50%", "0.5"),
            "80%": ("80%", "0.8"),
            "100%": ("100%", "1.0"),
        }
        for label, aliases in scenario_aliases.items():
            if not any(token in pressure_text for token in aliases):
                errors.append(
                    f"PUT P5_EXERCISE_PRESSURE missing {label} pressure scenario"
                )

    if review_ready and path_id == "DOWNWARD_REVISION":
        r7 = logic_by_id.get("R7_PRICE_TRANSLATION") or {}
        r7_ids = [str(x) for x in (r7.get("evidence_ids") or [])]
        if "PRELOADED:VALUATION_SCENARIO_GRID" not in r7_ids:
            errors.append(
                "DOWNWARD_REVISION R7_PRICE_TRANSLATION must cite "
                "PRELOADED:VALUATION_SCENARIO_GRID"
            )
        if len(r7.get("facts") or []) < 2:
            errors.append(
                "DOWNWARD_REVISION R7_PRICE_TRANSLATION must include "
                "concrete resolver scenario facts"
            )

    actual_reference = result.get("economic_judgment_reference")
    expected_judgment = task.get("economic_judgment") or {}
    reference_ok = False
    if isinstance(actual_reference, dict):
        same_identity = (
            actual_reference.get("economic_registry_run_id")
            == task.get("economic_registry_run_id")
            and actual_reference.get("market_snapshot_id")
            == task.get("market_snapshot_id")
        )
        nested = actual_reference.get("economic_judgment")
        if same_identity and nested == expected_judgment:
            reference_ok = True
        if same_identity:
            flattened = {
                key: value
                for key, value in actual_reference.items()
                if key not in {"economic_registry_run_id", "market_snapshot_id"}
            }
            if flattened == expected_judgment:
                reference_ok = True
    if not reference_ok:
        errors.append(
            "economic_judgment_reference must preserve the complete task "
            "economic judgment plus registry/snapshot identity"
        )

    for key in ("key_risks", "failure_conditions", "next_update_nodes"):
        if not isinstance(result.get(key), list):
            errors.append(f"{key} must be a list")

    if task.get("path_id") == "MATURITY_CASH":
        maturity_date = str(
            (task.get("market_state") or {}).get("maturity_date") or ""
        )
        for idx, node in enumerate(result.get("next_update_nodes") or []):
            if not isinstance(node, dict):
                continue
            event = str(node.get("event") or "")
            node_date = str(node.get("date") or "")
            if "到期日" in event and node_date and maturity_date and node_date != maturity_date:
                errors.append(
                    f"next_update_nodes[{idx}] labels {node_date} as 到期日, "
                    f"but task maturity_date is {maturity_date}"
                )

    evidence = result.get("key_evidence")
    if not isinstance(evidence, list):
        errors.append("key_evidence must be a list")
        evidence = []

    fetched_manifest_path = run_dir / "semantic_evidence_manifest.json"
    fetched_map: dict[str, dict[str, Any]] = {}
    if fetched_manifest_path.exists():
        manifest = _read_json(fetched_manifest_path)
        fetched_map = {
            str(item.get("evidence_id")): item
            for item in manifest.get("evidence", [])
            if item.get("evidence_id")
        }

    seen_ids: set[str] = set()
    for idx, item in enumerate(evidence):
        if not isinstance(item, dict):
            errors.append(f"key_evidence[{idx}] must be an object")
            continue
        for field in (
            "evidence_id",
            "source_type",
            "title",
            "source_date",
            "locator",
            "supports",
            "confidence",
        ):
            if field not in item:
                errors.append(f"key_evidence[{idx}] missing {field}")
        evidence_id = str(item.get("evidence_id") or "")
        if evidence_id:
            if evidence_id in seen_ids:
                errors.append(f"duplicate evidence_id={evidence_id}")
            seen_ids.add(evidence_id)

        if evidence_id in fetched_map:
            fetched = fetched_map[evidence_id]
            locator = str(item.get("locator") or "")
            valid = {
                str(fetched.get("pdf_url") or ""),
                str(fetched.get("detail_url") or ""),
            }
            valid.discard("")
            if locator and locator not in valid:
                errors.append(
                    f"evidence_id={evidence_id} locator does not match fetched evidence"
                )
        elif evidence_id and not evidence_id.startswith("PRELOADED:"):
            warnings.append(
                f"evidence_id={evidence_id} is not a fetched evidence id or PRELOADED id"
            )

    for idx, step in enumerate(logic_chain):
        if not isinstance(step, dict):
            continue
        step_evidence = step.get("evidence_ids")
        if not isinstance(step_evidence, list):
            continue
        if (
            review_ready
            and step.get("state") != "NOT_MATERIAL"
            and not step_evidence
        ):
            errors.append(
                f"review_ready logic_chain[{idx}] must cite evidence_ids "
                "unless state=NOT_MATERIAL"
            )
        for evidence_id in step_evidence:
            eid = str(evidence_id or "")
            if not eid:
                errors.append(f"logic_chain[{idx}] contains empty evidence_id")
            elif eid not in seen_ids and not eid.startswith("PRELOADED:"):
                errors.append(
                    f"logic_chain[{idx}] evidence_id={eid} is not present "
                    "in key_evidence and is not PRELOADED"
                )

    if errors:
        return _fail(run_dir, errors, warnings)

    normalized = run_dir / "path_research_result.json"
    if result_file.resolve() != normalized.resolve():
        normalized.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    validation = {
        "status": "PASS",
        "validated_at": _now(),
        "task_id": task.get("task_id"),
        "path_result_id": result.get("path_result_id"),
        "bond_code": str(task.get("bond_code") or "").zfill(6),
        "path_id": task.get("path_id"),
        "research_status": status,
        "review_ready": review_ready,
        "evidence_count": len(evidence),
        "unknown_a_count": len(unknown_a or []),
        "unknown_b_count": len(unknown_b or []),
        "logic_step_count": len(logic_chain),
        "logic_step_ids": logic_step_ids,
        "warnings": warnings,
        "result_ref": "path_research_result.json",
    }
    _write_json(run_dir / "path_research_validation.json", validation)
    return validation
