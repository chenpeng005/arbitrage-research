from __future__ import annotations

import json
import time
from typing import Any

import requests

from .provider import ProviderResponse


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: int = 60,
        max_retries: int = 2,
    ):
        if not api_key:
            raise ValueError("DeepSeek api_key is required")
        if not base_url:
            raise ValueError("DeepSeek base_url is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries

    @staticmethod
    def _normalize_messages(
        system_prompt: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

        for message in messages:
            role = message.get("role")

            if role == "assistant" and message.get("tool_calls"):
                tool_calls = []
                for call in message.get("tool_calls") or []:
                    arguments = call.get("arguments") or {}
                    if not isinstance(arguments, str):
                        arguments = json.dumps(
                            arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                        )
                    tool_calls.append(
                        {
                            "id": str(call.get("id") or ""),
                            "type": "function",
                            "function": {
                                "name": str(call.get("name") or ""),
                                "arguments": arguments,
                            },
                        }
                    )
                normalized.append(
                    {
                        "role": "assistant",
                        "content": message.get("content"),
                        "tool_calls": tool_calls,
                    }
                )
                continue

            if role == "tool":
                normalized.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(message.get("tool_call_id") or ""),
                        "name": str(message.get("name") or ""),
                        "content": str(message.get("content") or ""),
                    }
                )
                continue

            normalized.append(
                {
                    "role": str(role or "user"),
                    "content": message.get("content"),
                }
            )

        return normalized

    @staticmethod
    def _normalize_tools(
        tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        normalized = []
        for tool in tools:
            normalized.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "input_schema",
                            {"type": "object", "properties": {}},
                        ),
                    },
                }
            )
        return normalized

    def complete(
        self,
        *,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        output_schema: dict[str, Any],
        model_config: dict[str, Any],
    ) -> ProviderResponse:
        model = model_config.get("model")
        if not model:
            raise ValueError("DeepSeek model is required")

        schema_contract = json.dumps(
            output_schema,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        effective_system_prompt = (
            system_prompt
            + "\n\n## FINAL STRUCTURED OUTPUT CONTRACT\n"
            + "When you are ready to return the final answer (not a tool call), "
              "return exactly one JSON object conforming to this schema. "
              "Do not omit required fields.\n"
            + schema_contract
        )

        payload: dict[str, Any] = {
            "model": model,
            "messages": self._normalize_messages(
                effective_system_prompt,
                messages,
            ),
            "stream": False,
            "response_format": {"type": "json_object"},
        }

        if tools:
            payload["tools"] = self._normalize_tools(tools)
            payload["tool_choice"] = "auto"

        if model_config.get("temperature") is not None:
            payload["temperature"] = model_config["temperature"]
        if model_config.get("max_tokens") is not None:
            payload["max_tokens"] = model_config["max_tokens"]

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        response: requests.Response | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = requests.post(
                    url,
                    headers=headers,
                    json=payload,
                    timeout=self.timeout_seconds,
                )

                if response.status_code == 429 or 500 <= response.status_code < 600:
                    if attempt < self.max_retries:
                        time.sleep(1.5 * (attempt + 1))
                        continue

                response.raise_for_status()
                break

            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    raise
                time.sleep(1.5 * (attempt + 1))

        if response is None:
            if last_error:
                raise last_error
            raise RuntimeError("DeepSeek request did not produce a response")

        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("DeepSeek response has no choices")

        choice = choices[0]
        message = choice.get("message") or {}
        raw_tool_calls = message.get("tool_calls") or []
        tool_calls: list[dict[str, Any]] = []

        for raw_call in raw_tool_calls:
            function = raw_call.get("function") or {}
            raw_arguments = function.get("arguments") or "{}"
            try:
                arguments = (
                    raw_arguments
                    if isinstance(raw_arguments, dict)
                    else json.loads(raw_arguments)
                )
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"DeepSeek tool arguments are not valid JSON: {raw_arguments!r}"
                ) from exc

            tool_calls.append(
                {
                    "id": str(raw_call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "arguments": arguments,
                }
            )

        structured_output = None
        content = message.get("content")

        if not tool_calls:
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError(
                    "DeepSeek returned neither tool_calls nor JSON content"
                )
            try:
                structured_output = json.loads(content)
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "DeepSeek final content is not valid JSON"
                ) from exc
            if not isinstance(structured_output, dict):
                raise RuntimeError(
                    "DeepSeek structured output must be a JSON object"
                )

        return ProviderResponse(
            provider="deepseek",
            model=str(data.get("model") or model),
            request_id=data.get("id"),
            finish_reason=str(choice.get("finish_reason") or ""),
            structured_output=structured_output,
            tool_calls=tool_calls,
            usage=data.get("usage") or {},
            raw_response=data,
        )
