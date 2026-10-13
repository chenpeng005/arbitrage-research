import os
import unittest
from unittest.mock import patch

from runtime.ai_runtime.provider_factory import build_provider
from runtime.intelligence_radar.filtering import build_provider_from_env


class AIProviderAliasTest(unittest.TestCase):
    def test_engine_accepts_volcengine_agent_plan(self) -> None:
        provider = build_provider(
            "volcengine-agent-plan",
            {
                "api_key": "test-key",
                "base_url": "https://ark.example/api/plan/v3",
            },
        )
        self.assertEqual(provider.name, "volcengine-agent-plan")
        self.assertEqual(provider.base_url, "https://ark.example/api/plan/v3")

    def test_radar_preserves_configured_provider_name(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AI_PROVIDER": "volcengine-agent-plan",
                "AI_API_KEY": "test-key",
                "AI_BASE_URL": "https://ark.example/api/plan/v3",
            },
            clear=False,
        ):
            provider = build_provider_from_env()
        self.assertEqual(provider.name, "volcengine-agent-plan")


if __name__ == "__main__":
    unittest.main()
