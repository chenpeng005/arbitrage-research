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
                    "object_name",
                    "node_title",
                    "finding_type",
                    "what_happened",
                    "ai_understanding",
                    "current_judgment",
                    "worth_follow_up",
                    "supporting_segment_ids",
                ],
                "properties": {
                    "question_id": {"type": "string"},
                    "object_name": {"type": "string"},
                    "node_title": {"type": "string"},
                    "finding_type": {
                        "type": "string",
                        "enum": sorted(FINDING_TYPES),
                    },
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
    capsule = str(candidate.get("context_capsule") or "").strip()
    return {
        "question_id": candidate["question_id"],
        "title": candidate["title"],
        "url": candidate["url"],
        "category": candidate.get("category"),
        "question_author": candidate.get("question_author"),
        "question_published_at": candidate.get("question_published_at"),
        "activity_at": candidate.get("activity_at"),
        "coverage_warning": candidate.get("coverage_warning"),
        "context_capsule": capsule or None,
        "context_capsule_version": candidate.get("context_capsule_version"),
        # A capsule is derived from already-retained Final nodes and is context only.
        # When present we avoid resending the long original post to Broad AI.
        "context_segments": [] if capsule else candidate.get("context_segments", []),
        "daily_segments": candidate.get("daily_segments", []),
    }


def normalize_finding_question_ids(
    input_payload: dict[str, Any],
    output: dict[str, Any],
) -> list[dict[str, str]]:
    """Repair a model qid only when cited segment ownership is unambiguous."""
    segment_owners: dict[str, set[str]] = {}
    for candidate in input_payload.get("candidates", []):
        qid = str(candidate["question_id"])
        segments = (
            list(candidate.get("context_segments", []))
            + list(candidate.get("daily_segments", []))
        )
        for segment in segments:
            segment_owners.setdefault(str(segment["segment_id"]), set()).add(qid)

    repairs: list[dict[str, str]] = []
    for finding in output.get("findings", []):
        if not isinstance(finding, dict):
            continue
        ids = finding.get("supporting_segment_ids")
        if not isinstance(ids, list) or not ids:
            continue
        owners: set[str] = set()
        resolvable = True
        for segment_id in ids:
            matches = segment_owners.get(str(segment_id), set())
            if len(matches) != 1:
                resolvable = False
                break
            owners.update(matches)
        if not resolvable or len(owners) != 1:
            continue
        owner = next(iter(owners))
        original = str(finding.get("question_id") or "")
        if original != owner:
            finding["question_id"] = owner
            repairs.append(
                {
                    "from_question_id": original,
                    "to_question_id": owner,
                }
            )
    return repairs


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

        object_name = str(finding.get("object_name") or "").strip()
        node_title = str(finding.get("node_title") or "").strip()
        if len(object_name) < 2:
            errors.append(f"findings[{idx}] object_name too short")
        if len(object_name) > 80:
            errors.append(f"findings[{idx}] object_name too long")
        if len(node_title) < 4:
            errors.append(f"findings[{idx}] node_title too short")
        if len(node_title) > 120:
            errors.append(f"findings[{idx}] node_title too long")

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


def _evidence_rows(
    *,
    source: str,
    candidate: dict[str, Any],
    segment_ids: list[str],
) -> list[dict[str, Any]]:
    segment_map = {
        str(seg["segment_id"]): seg
        for seg in (
            list(candidate.get("context_segments", []))
            + list(candidate.get("daily_segments", []))
        )
    }
    selected = [
        segment_map[str(segment_id)]
        for segment_id in segment_ids
        if str(segment_id) in segment_map
    ]
    primary_index = 0
    for idx, segment in enumerate(selected):
        if bool(segment.get("is_daily")):
            primary_index = idx
            break

    rows: list[dict[str, Any]] = []
    for idx, segment in enumerate(selected):
        text = str(segment.get("text") or "").strip()
        if len(text) > 900:
            text = text[:900].rstrip() + "…"
        rows.append(
            {
                "source": source,
                "author": segment.get("author"),
                "published_at": segment.get("published_at"),
                "source_title": candidate.get("title"),
                "source_url": candidate.get("url"),
                "locator_kind": segment.get("kind"),
                "locator_id": segment.get("locator_id") or segment.get("segment_id"),
                "locator_url": segment.get("locator_url") or candidate.get("url"),
                "excerpt": text or None,
                "is_primary": idx == primary_index,
            }
        )
    return rows


def _evidence_excerpt(evidence: list[dict[str, Any]]) -> str:
    excerpts = []
    for row in evidence:
        text = str(row.get("excerpt") or "").strip()
        if not text:
            continue
        if len(text) > 320:
            text = text[:320].rstrip() + "…"
        who = str(row.get("author") or "")
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
        segment_ids = [str(x) for x in finding["supporting_segment_ids"]]
        evidence = _evidence_rows(
            source=str(input_payload["source"]),
            candidate=candidate,
            segment_ids=segment_ids,
        )
        primary = next(
            (row for row in evidence if row.get("is_primary")),
            evidence[0] if evidence else {},
        )
        rows.append(
            {
                "item_key": qid,
                "question_id": qid,
                "object_name": str(finding["object_name"]).strip(),
                "node_title": str(finding["node_title"]).strip(),
                "title": candidate["title"],
                "url": candidate["url"],
                "author": primary.get("author")
                or candidate.get("question_author")
                or candidate.get("feed_actor"),
                "observed_at": primary.get("published_at")
                or (max(daily_times) if daily_times else candidate.get("activity_at")),
                "finding_type": finding["finding_type"],
                "what_happened": finding["what_happened"].strip(),
                "ai_understanding": finding["ai_understanding"].strip(),
                "current_judgment": finding["current_judgment"].strip(),
                "worth_follow_up": bool(finding["worth_follow_up"]),
                "evidence_excerpt": _evidence_excerpt(evidence),
                "evidence": evidence,
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
    normalize_finding_question_ids(input_payload, response.structured_output)
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
                    "每个 finding 必须给出稳定的 object_name 与聚焦当天新增的 node_title；"
                    "supporting_segment_ids 必须来自该 finding 对应 question_id 自己的 "
                    "context_segments 或 daily_segments，且至少一个 is_daily=true。"
                    "校验错误：" + "; ".join(errors)
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
            raise RuntimeError(
                "AI provider returned no structured output on validation retry"
            )
        normalize_finding_question_ids(input_payload, response.structured_output)
        errors = validate_filter_output(input_payload, response.structured_output)
        if errors:
            raise ValueError(
                "AI filter validation failed after retry: " + "; ".join(errors)
            )
    rows = enrich_findings(input_payload, response.structured_output)
    source_candidate_map = {str(row["question_id"]): row for row in candidates}
    for row in rows:
        candidate = source_candidate_map.get(str(row.get("question_id") or ""), {})
        row["discovery_paths"] = list(candidate.get("discovery_paths", []))
        row["author_lane_authors"] = list(candidate.get("author_lane_authors", []))
    return rows
