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


def get_task_spec(task_type: str, root: Path) -> AITaskSpec:
    if task_type == "MARKET_MAP_SEMANTIC_AUDIT":
        return market_map_semantic_audit_spec(root)
    raise ValueError(f"Unsupported AI task_type: {task_type}")
