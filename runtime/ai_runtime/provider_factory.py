from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .deepseek_provider import DeepSeekProvider
from .mock_provider import MockProvider
from .provider import AIProvider


def build_provider(provider_name: str, provider_config: dict[str, Any]) -> AIProvider:
    if provider_name == "mock":
        response_file = provider_config.get("response_file")
        if not response_file:
            raise ValueError("Mock provider requires response_file")
        return MockProvider(Path(response_file))

    if provider_name in {"deepseek", "volcengine-agent-plan"}:
        return DeepSeekProvider(
            api_key=str(
                provider_config.get("api_key")
                or os.environ.get("AI_API_KEY")
                or ""
            ),
            base_url=str(
                provider_config.get("base_url")
                or os.environ.get("AI_BASE_URL")
                or ""
            ),
            provider_name=provider_name,
            timeout_seconds=int(provider_config.get("timeout_seconds") or 90),
            max_retries=int(provider_config.get("max_retries") or 2),
        )

    raise ValueError(f"Unsupported AI provider: {provider_name}")
