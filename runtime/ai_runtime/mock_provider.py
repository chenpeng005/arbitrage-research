from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .provider import ProviderResponse


class MockProvider:
    name = "mock"

    def __init__(self, response_file: Path):
        self.response_file = response_file

    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model_config: dict[str, Any],
    ) -> ProviderResponse:
        payload = json.loads(self.response_file.read_text(encoding="utf-8"))
        return ProviderResponse(
            provider="mock",
            model=model_config.get("model", "mock-semantic-audit-v0"),
            request_id="mock-request",
            finish_reason="stop",
            structured_output=payload,
            tool_calls=[],
            usage={"input_tokens": 0, "output_tokens": 0},
            raw_response={
                "fixture": str(self.response_file),
                "structured_output": payload,
            },
        )
