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
                "resolutions": {"type": "array"},
            },
        },
        allowed_tools=[],
        validator="runtime.market_map.semantic_resolution.validate_resolution",
        max_tool_rounds=0,
        timeout_seconds=60,
    )


def get_task_spec(task_type: str, root: Path) -> AITaskSpec:
    if task_type == "MARKET_MAP_SEMANTIC_AUDIT":
        return market_map_semantic_audit_spec(root)
    raise ValueError(f"Unsupported AI task_type: {task_type}")
