from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from runtime.ai_runtime.deepseek_provider import DeepSeekProvider

FINDING_TYPES = {
    "NEW_MECHANISM",
    "NEW_CASE",
    "REAL_TEST",
    "COUNTEREVIDENCE",
    "RULE_CHANGE",
    "EXECUTION_ISSUE",
    "DATA_ISSUE",
    "ANOMALY",
    "OTHER",
}

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["run_date", "source", "batch_id", "findings"],
    "properties": {
        "run_date": {"type": "string"},
        "source": {"type": "string"},
        "batch_id": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "question_id",
                    "finding_type",
                    "what_happened",
                    "ai_understanding",
                    "current_judgment",
                    "worth_follow_up",
                    "supporting_segment_ids",
                ],
                "properties": {
                    "question_id": {"type": "string"},
                    "finding_type": {"type": "string", "enum": sorted(FINDING_TYPES)},
                    "what_happened": {"type": "string"},
                    "ai_understanding": {"type": "string"},
                    "current_judgment": {"type": "string"},
                    "worth_follow_up": {"type": "boolean"},
                    "supporting_segment_ids": {
                        "type": "array",
                        "minItems": 1,
                        "items": {"type": "string"},
                    },
                },
            },
        },
    },
}

PROMPT_PATH = Path(__file__).with_name("prompt_daily_filter.md")


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


def build_provider_from_env() -> DeepSeekProvider:
    return DeepSeekProvider(
        api_key=os.environ.get("AI_API_KEY", ""),
        base_url=os.environ.get("AI_BASE_URL", ""),
        timeout_seconds=120,
        max_retries=2,
    )


def _candidate_input(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "question_id": candidate["question_id"],
        "title": candidate["title"],
        "url": candidate["url"],
        "category": candidate.get("category"),
        "question_author": candidate.get("question_author"),
        "question_published_at": candidate.get("question_published_at"),
        "activity_at": candidate.get("activity_at"),
        "coverage_warning": candidate.get("coverage_warning"),
        "context_segments": candidate.get("context_segments", []),
        "daily_segments": candidate.get("daily_segments", []),
    }


def validate_filter_output(
    input_payload: dict[str, Any],
    output: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    for key in ("run_date", "source", "batch_id"):
        if output.get(key) != input_payload.get(key):
            errors.append(f"{key} mismatch")

    candidates = {
        str(row["question_id"]): row
        for row in input_payload.get("candidates", [])
    }
    findings = output.get("findings")
    if not isinstance(findings, list):
        return errors + ["findings must be an array"]

    per_question: dict[str, int] = {}
    for idx, finding in enumerate(findings):
        if not isinstance(finding, dict):
            errors.append(f"findings[{idx}] must be object")
            continue
        qid = str(finding.get("question_id") or "")
        candidate = candidates.get(qid)
        if candidate is None:
            errors.append(f"findings[{idx}] unknown question_id")
            continue

        per_question[qid] = per_question.get(qid, 0) + 1
        if per_question[qid] > 3:
            errors.append(f"question {qid} has more than 3 findings")
        if finding.get("finding_type") not in FINDING_TYPES:
            errors.append(f"findings[{idx}] invalid finding_type")

        segment_map = {
            str(seg["segment_id"]): seg
            for seg in (
                list(candidate.get("context_segments", []))
                + list(candidate.get("daily_segments", []))
            )
        }
        ids = finding.get("supporting_segment_ids")
        if not isinstance(ids, list) or not ids:
            errors.append(f"findings[{idx}] missing supporting_segment_ids")
            continue
        missing = [str(x) for x in ids if str(x) not in segment_map]
        if missing:
            errors.append(f"findings[{idx}] unknown supporting segments: {missing}")
        valid_segments = [
            segment_map[str(x)] for x in ids if str(x) in segment_map
        ]
        if valid_segments and not any(bool(seg.get("is_daily")) for seg in valid_segments):
            errors.append(f"findings[{idx}] must cite at least one daily segment")

        for field in ("what_happened", "ai_understanding", "current_judgment"):
            if len(str(finding.get(field) or "").strip()) < 4:
                errors.append(f"findings[{idx}] {field} too short")
    return errors


def _evidence_excerpt(
    candidate: dict[str, Any],
    segment_ids: list[str],
) -> str:
    segment_map = {
        str(seg["segment_id"]): seg
        for seg in (
            list(candidate.get("context_segments", []))
            + list(candidate.get("daily_segments", []))
        )
    }
    excerpts = []
    for segment_id in segment_ids:
        segment = segment_map.get(str(segment_id))
        if not segment:
            continue
        text = str(segment.get("text") or "").strip()
        if len(text) > 320:
            text = text[:320].rstrip() + "…"
        if text:
            who = str(segment.get("author") or "")
            excerpts.append((who + "：" if who else "") + text)
    return " / ".join(excerpts[:3])


def enrich_findings(
    input_payload: dict[str, Any],
    output: dict[str, Any],
) -> list[dict[str, Any]]:
    candidate_map = {
        str(row["question_id"]): row
        for row in input_payload["candidates"]
    }
    rows: list[dict[str, Any]] = []
    for finding in output.get("findings", []):
        qid = str(finding["question_id"])
        candidate = candidate_map[qid]
        daily_times = [
            str(seg.get("published_at") or "")
            for seg in candidate.get("daily_segments", [])
            if seg.get("published_at")
        ]
        rows.append(
            {
                "item_key": qid,
                "question_id": qid,
                "title": candidate["title"],
                "url": candidate["url"],
                "author": candidate.get("question_author") or candidate.get("feed_actor"),
                "observed_at": max(daily_times) if daily_times else candidate.get("activity_at"),
                "finding_type": finding["finding_type"],
                "what_happened": finding["what_happened"].strip(),
                "ai_understanding": finding["ai_understanding"].strip(),
                "current_judgment": finding["current_judgment"].strip(),
                "worth_follow_up": bool(finding["worth_follow_up"]),
                "evidence_excerpt": _evidence_excerpt(
                    candidate,
                    [str(x) for x in finding["supporting_segment_ids"]],
                ),
            }
        )
    return rows


def filter_batch(
    *,
    run_date: str,
    source: str,
    batch_id: str,
    candidates: list[dict[str, Any]],
    provider: Provider,
    model: str,
) -> list[dict[str, Any]]:
    input_payload = {
        "run_date": run_date,
        "source": source,
        "batch_id": batch_id,
        "candidates": [_candidate_input(x) for x in candidates],
    }
    messages = [
        {
            "role": "user",
            "content": json.dumps(input_payload, ensure_ascii=False),
        }
    ]
    response = provider.complete(
        system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
        messages=messages,
        tools=[],
        output_schema=OUTPUT_SCHEMA,
        model_config={"model": model, "temperature": 0.1, "max_tokens": 8000},
    )
    if response.structured_output is None:
        raise RuntimeError("AI provider returned no structured output")
    errors = validate_filter_output(input_payload, response.structured_output)
    if errors:
        retry_messages = messages + [
            {
                "role": "assistant",
                "content": json.dumps(response.structured_output, ensure_ascii=False),
            },
            {
                "role": "user",
                "content": (
                    "上一轮输出未通过程序校验，请重新输出完整结果。"
                    "每个 finding 的 supporting_segment_ids 必须来自该 finding "
                    "对应 question_id 自己的 context_segments 或 daily_segments，"
                    "且至少一个 is_daily=true。校验错误：" + "; ".join(errors)
                ),
            },
        ]
        response = provider.complete(
            system_prompt=PROMPT_PATH.read_text(encoding="utf-8"),
            messages=retry_messages,
            tools=[],
            output_schema=OUTPUT_SCHEMA,
            model_config={"model": model, "temperature": 0.0, "max_tokens": 8000},
        )
        if response.structured_output is None:
            raise RuntimeError("AI provider returned no structured output on validation retry")
        errors = validate_filter_output(input_payload, response.structured_output)
        if errors:
            raise ValueError(
                "AI filter validation failed after retry: " + "; ".join(errors)
            )
    return enrich_findings(input_payload, response.structured_output)
