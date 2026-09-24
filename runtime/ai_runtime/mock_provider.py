from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .provider import ProviderResponse


class MockProvider:
    name = "mock"

    def __init__(self, response_file: Path):
        self.response_file = response_file
        self._round = 0
        self._payload = json.loads(self.response_file.read_text(encoding="utf-8"))

    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model_config: dict[str, Any],
    ) -> ProviderResponse:
        payload = self._payload

        if isinstance(payload, dict) and isinstance(payload.get("rounds"), list):
            rounds = payload["rounds"]
            if self._round >= len(rounds):
                raise RuntimeError("Mock scripted provider exhausted all rounds")
            current = rounds[self._round]
            self._round += 1

            tool_calls = current.get("tool_calls") or []
            structured_output = current.get("structured_output")
            finish_reason = "tool_calls" if tool_calls else "stop"

            return ProviderResponse(
                provider="mock",
                model=model_config.get("model", "mock-semantic-audit-v0"),
                request_id=f"mock-request-{self._round}",
                finish_reason=finish_reason,
                structured_output=structured_output,
                tool_calls=tool_calls,
                usage={"input_tokens": 0, "output_tokens": 0},
                raw_response={
                    "fixture": str(self.response_file),
                    "round": self._round,
                    "scripted_round": current,
                },
            )

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
