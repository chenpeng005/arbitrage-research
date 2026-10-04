from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["run_date", "source", "keep_ids"],
    "properties": {
        "run_date": {"type": "string"},
        "source": {"type": "string"},
        "keep_ids": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
}

PROMPT_PATH = Path(__file__).with_name("prompt_final_gate.md")


class Provider(Protocol):
    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model_config: dict[str, Any],
    ): ...


def _gate_input(
    run_date: str,
    source: str,
    broad_findings: list[dict[str, Any]],
) -> dict[str, Any]:
    rows = []
    for idx, row in enumerate(broad_findings, start=1):
        rows.append(
            {
                "broad_id": f"B{idx:03d}",
                "question_id": row.get("question_id"),
                "title": row.get("title"),
                "finding_type": row.get("finding_type"),
                "what_happened": row.get("what_happened"),
                "ai_understanding": row.get("ai_understanding"),
                "current_judgment": row.get("current_judgment"),
                "worth_follow_up": bool(row.get("worth_follow_up")),
                "evidence_excerpt": row.get("evidence_excerpt"),
            }
        )
    return {
        "run_date": run_date,
        "source": source,
        "broad_findings": rows,
    }


def validate_final_gate_output(
    input_payload: dict[str, Any],
    output: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for key in ("run_date", "source"):
        if output.get(key) != input_payload.get(key):
            errors.append(f"{key} mismatch")

    keep_ids = output.get("keep_ids")
    if not isinstance(keep_ids, list):
        return errors + ["keep_ids must be an array"]

    allowed = {
        str(row["broad_id"])
        for row in input_payload.get("broad_findings", [])
    }
    seen: set[str] = set()
    for idx, raw_id in enumerate(keep_ids):
        item_id = str(raw_id)
        if item_id not in allowed:
            errors.append(f"keep_ids[{idx}] unknown broad_id")
        if item_id in seen:
            errors.append(f"keep_ids[{idx}] duplicate broad_id")
        seen.add(item_id)
    return errors


def apply_final_gate_selection(
    input_payload: dict[str, Any],
    broad_findings: list[dict[str, Any]],
    output: dict[str, Any],
) -> list[dict[str, Any]]:
    keep = {str(x) for x in output.get("keep_ids", [])}
    selected = []
    for idx, row in enumerate(broad_findings, start=1):
        if f"B{idx:03d}" in keep:
            selected.append(row)
    return selected


def select_final_findings(
    *,
    run_date: str,
    source: str,
    broad_findings: list[dict[str, Any]],
    provider: Provider,
    model: str,
) -> list[dict[str, Any]]:
    if not broad_findings:
        return []

    input_payload = _gate_input(run_date, source, broad_findings)
    response = provider.complete(
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        messages=[
            {
                "role": "user",
                "content": json.dumps(input_payload, ensure_ascii=False),
            }
        ],
        tools=[],
        output_schema=OUTPUT_SCHEMA,
        model_config={
            "model": model,
            "temperature": 0.0,
            "max_tokens": 3000,
        },
    )
    if response.structured_output is None:
        raise RuntimeError("AI final gate returned no structured output")
    errors = validate_final_gate_output(
        input_payload,
        response.structured_output,
    )
    if errors:
        raise ValueError(
            "AI final gate validation failed: " + "; ".join(errors)
        )
    return apply_final_gate_selection(
        input_payload,
        broad_findings,
        response.structured_output,
    )
