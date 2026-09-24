from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class ProviderResponse:
    provider: str
    model: str
    request_id: str | None
    finish_reason: str
    structured_output: dict[str, Any] | None
    tool_calls: list[dict[str, Any]]
    usage: dict[str, Any]
    raw_response: dict[str, Any]


class AIProvider(Protocol):
    name: str

    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model_config: dict[str, Any],
    ) -> ProviderResponse:
        ...
