from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AITaskSpec:
    task_type: str
    prompt_path: Path
    output_schema: dict[str, Any]
    allowed_tools: list[dict[str, Any]]
    validator: str
    max_tool_rounds: int
    timeout_seconds: int


def market_map_semantic_audit_spec(root: Path) -> AITaskSpec:
    return AITaskSpec(
        task_type="MARKET_MAP_SEMANTIC_AUDIT",
        prompt_path=root / "runtime" / "ai_runtime" / "prompts" / "market_map_semantic_audit_v0.1.md",
        output_schema={
            "type": "object",
            "required": [
                "request_run_id",
                "market_cutoff",
                "resolver_type",
                "completed_at",
                "resolutions",
            ],
            "properties": {
                "request_run_id": {"type": "string"},
                "market_cutoff": {"type": "string"},
                "resolver_type": {"type": "string"},
                "completed_at": {"type": "string"},
                "resolutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "conflict_id",
                            "bond_code",
                            "field",
                            "status",
                            "resolved_value",
                            "evidence",
                            "confidence",
                            "reason_short",
                        ],
                        "properties": {
                            "conflict_id": {"type": "string"},
                            "bond_code": {"type": "string"},
                            "field": {"type": "string"},
                            "status": {
                                "type": "string",
                                "enum": [
                                    "RESOLVED",
                                    "INSUFFICIENT_EVIDENCE",
                                    "NOT_APPLICABLE",
                                ],
                            },
                            "resolved_value": {},
                            "effective_from": {
                                "type": ["string", "null"],
                            },
                            "evidence": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": [
                                        "evidence_id",
                                        "source_type",
                                        "title",
                                        "published_at",
                                        "locator",
                                    ],
                                    "properties": {
                                        "evidence_id": {"type": "string"},
                                        "source_type": {"type": "string"},
                                        "title": {"type": "string"},
                                        "published_at": {"type": "string"},
                                        "locator": {"type": "string"},
                                    },
                                },
                            },
                            "confidence": {"type": "string"},
                            "reason_short": {"type": "string"},
                        },
                    },
                },
            },
        },
        allowed_tools=[
            {
                "name": "evidence_search",
                "description": "只围绕本次 conflict 在巨潮资讯正式公告中搜索候选证据。股票身份由 Program 根据 conflict_id 决定，AI 不能自行扩展对象。",
                "input_schema": {
                    "type": "object",
                    "required": ["conflict_id"],
                    "properties": {
                        "conflict_id": {"type": "string"},
                        "keyword": {"type": "string"},
                        "start_date": {"type": "string", "description": "YYYY-MM-DD，可省略"},
                        "end_date": {"type": "string", "description": "YYYY-MM-DD，可省略"},
                    },
                },
            },
            {
                "name": "evidence_fetch",
                "description": "读取本 AI Job 之前 evidence_search 返回过的巨潮公告 PDF，并由 Program 保存 PDF、提取文本、计算 hash。",
                "input_schema": {
                    "type": "object",
                    "required": ["evidence_id"],
                    "properties": {
                        "evidence_id": {"type": "string"},
                    },
                },
            },
        ],
        validator="runtime.market_map.semantic_resolution.validate_resolution",
        max_tool_rounds=5,
        timeout_seconds=120,
    )


def path_research_spec(root: Path) -> AITaskSpec:
    return AITaskSpec(
        task_type="PATH_RESEARCH",
        prompt_path=(
            root / "runtime" / "ai_runtime" / "prompts" / "path_research_v1.md"
        ),
        output_schema={
            "type": "object",
            "required": [
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
                "engineering_anchor_reference",
                "economic_judgment_reference",
            ],
            "properties": {
                "path_result_id": {"type": "string"},
                "task_id": {"type": "string"},
                "trigger_key": {"type": "string"},
                "bond_code": {"type": "string"},
                "bond_name": {"type": "string"},
                "path_id": {
                    "type": "string",
                    "enum": ["MATURITY_CASH", "PUT", "DOWNWARD_REVISION"],
                },
                "research_cutoff": {"type": "string"},
                "path_research_canonical_path": {"type": "string"},
                "knowledge_commit_sha": {"type": "string"},
                "review_ready": {"type": "boolean"},
                "research_status": {
                    "type": "string",
                    "enum": ["COMPLETED", "NEEDS_EVIDENCE", "UNRESOLVED"],
                },
                "summary": {
                    "type": "object",
                    "required": [
                        "core_conclusion",
                        "why",
                        "economic_result",
                        "main_risks",
                        "next_focus",
                    ],
                    "properties": {
                        "core_conclusion": {"type": "string"},
                        "why": {"type": "array", "items": {"type": "string"}},
                        "economic_result": {"type": "string"},
                        "main_risks": {"type": "array", "items": {"type": "string"}},
                        "next_focus": {"type": "string"},
                    },
                },
                "logic_chain": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "step_id",
                            "title",
                            "question",
                            "state",
                            "answer",
                            "facts",
                            "reasoning",
                            "conclusion",
                            "evidence_ids",
                        ],
                        "properties": {
                            "step_id": {"type": "string"},
                            "title": {"type": "string"},
                            "question": {"type": "string"},
                            "state": {
                                "type": "string",
                                "enum": [
                                    "KNOWN",
                                    "DERIVED",
                                    "MIXED",
                                    "NOT_MATERIAL",
                                    "UNKNOWN_A",
                                    "UNKNOWN_B",
                                ],
                            },
                            "answer": {"type": "string"},
                            "facts": {"type": "array", "items": {"type": "string"}},
                            "reasoning": {"type": "string"},
                            "conclusion": {"type": "string"},
                            "evidence_ids": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "fact_spine": {"type": "object"},
                "judgments": {"type": "object"},
                "key_evidence": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": [
                            "evidence_id",
                            "source_type",
                            "title",
                            "source_date",
                            "locator",
                            "supports",
                            "confidence",
                        ],
                        "properties": {
                            "evidence_id": {"type": "string"},
                            "source_type": {"type": "string"},
                            "title": {"type": "string"},
                            "source_date": {"type": ["string", "null"]},
                            "locator": {"type": ["string", "null"]},
                            "supports": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "confidence": {"type": "string"},
                        },
                    },
                },
                "unknown_a": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "unknown_b": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "key_risks": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "failure_conditions": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "next_update_nodes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["date", "event"],
                        "properties": {
                            "date": {"type": ["string", "null"]},
                            "event": {"type": "string"},
                        },
                    },
                },
                "economic_status_at_research": {
                    "type": "string",
                    "enum": ["KEEP"],
                },
                "engineering_anchor_reference": {
                    "type": "object",
                    "required": [
                        "current_event_state",
                        "market_state",
                        "anchor_statement",
                    ],
                    "properties": {
                        "current_event_state": {"type": ["string", "null"]},
                        "market_state": {"type": "object"},
                        "anchor_statement": {"type": "string"},
                    },
                },
                "economic_judgment_reference": {"type": "object"},
            },
        },
        allowed_tools=[
            {
                "name": "path_evidence_search",
                "description": (
                    "只围绕当前 Path Research task 检索正式公告证据。"
                    "优先使用 Evidence Pack 已冻结的公告候选；无匹配时才做窄范围回退搜索。"
                    "Program 固定 stock_code，AI 不能扩展对象。"
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string"},
                        "start_date": {
                            "type": "string",
                            "description": "YYYY-MM-DD，可省略",
                        },
                        "end_date": {
                            "type": "string",
                            "description": "YYYY-MM-DD，可省略",
                        },
                    },
                },
            },
            {
                "name": "path_evidence_fetch",
                "description": (
                    "读取本 AI Job 之前 path_evidence_search 返回过的正式公告。"
                    "对冻结候选优先抓正文并在可用时读取 PDF 全文，"
                    "Program 保存 PDF、文本和 hash。"
                ),
                "input_schema": {
                    "type": "object",
                    "required": ["evidence_id"],
                    "properties": {
                        "evidence_id": {"type": "string"},
                    },
                },
            },
        ],
        validator=(
            "runtime.opportunity.path_research_validation."
            "validate_path_research_result"
        ),
        max_tool_rounds=4,
        timeout_seconds=180,
    )


def get_task_spec(task_type: str, root: Path) -> AITaskSpec:
    if task_type == "MARKET_MAP_SEMANTIC_AUDIT":
        return market_map_semantic_audit_spec(root)
    if task_type == "PATH_RESEARCH":
        return path_research_spec(root)
    raise ValueError(f"Unsupported AI task_type: {task_type}")
